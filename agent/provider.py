"""ERC-8183 seller side: signed quotes, funded-job delivery and settlement.

Flow (BSC testnet, BNB's AgenticCommerce kernel; Pokter and other escrow marketplaces fund it):
negotiate -> buyer funds escrow -> notify_funded (delivered right away) or the 10-minute
GitHub Actions poll -> verify the job is ours, FUNDED and carries a quote we signed -> build the
assessment -> pin it to IPFS and submit its manifest hash -> after the dispute window, settle so
the job ends COMPLETED. No local state: every step re-reads the chain.
"""

import asyncio
import json
import logging
import time

from bnbagent import EVMWalletProvider
from bnbagent.erc8183 import ERC8183JobOps, NegotiationHandler
from bnbagent.storage import LocalStorageProvider

from . import chain, report, reputation, settings

log = logging.getLogger("halong.provider")

DEFAULT_TERMS = {
    "deliverables": "A JSON yield assessment with recommendation, expected earnings, risks and data sources.",
    "quality_standards": "Read-only analysis from live Venus mainnet data. Execute no transaction for the buyer.",
}
SETTLE_GRACE_SECONDS = 30


def _storage():
    if not settings.STORAGE_API_KEY:
        return LocalStorageProvider(str(settings.DATA_DIR / "deliverables"))
    from bnbagent.storage import IPFSStorageProvider

    class GatewayIPFSStorage(IPFSStorageProvider):
        """Publish the HTTPS gateway URL instead of ipfs://, so any buyer can fetch the manifest."""

        async def upload(self, data: dict, filename: str | None = None) -> str:
            return self.get_gateway_url(await super().upload(data, filename))

    return GatewayIPFSStorage(
        pinning_api_url="https://api.pinata.cloud/pinning/pinJSONToIPFS",
        pinning_api_key=settings.STORAGE_API_KEY,
        gateway_url=settings.STORAGE_GATEWAY_URL,
    )


class Provider:
    def __init__(self):
        wallet = EVMWalletProvider(password=settings.WALLET_PASSWORD, private_key=settings.require_agent_key(),
                                   persist=False)
        self.address = wallet.address
        self.ops = ERC8183JobOps(
            wallet, settings.NETWORK, storage_provider=_storage(),
            service_price=settings.JOB_PRICE_WEI, agent_url=f"{settings.BASE_URL}/erc8183",
        )
        self.client = self.ops.erc8183_client
        endpoint = getattr(self.client.commerce.w3.provider, "endpoint_uri", None)
        if endpoint != settings.TESTNET_RPC:
            log.warning("SDK RPC %s differs from %s: nonces are not shared", endpoint, settings.TESTNET_RPC)
        self.negotiator = NegotiationHandler(
            service_price=settings.JOB_PRICE_WEI, currency=self.client.payment_token, wallet_provider=wallet,
            chain_id=settings.CHAIN_ID, verifying_contract=self.client.commerce.address, quote_ttl_seconds=900,
        )

    # ------------------------------------------------------------------ quotes

    def negotiate(self, data: dict) -> dict:
        task = (data.get("task_description") or data.get("task") or "").strip() or report.DEFAULT_TASK
        terms = data.get("terms") if isinstance(data.get("terms"), dict) else DEFAULT_TERMS
        if not report.in_scope(task):
            return {"request": {"task_description": task},
                    "response": {"accepted": False, "reason": report.SCOPE_HINT}}
        envelope = self.negotiator.negotiate({"task_description": task, "terms": {**DEFAULT_TERMS, **terms}}).to_dict()
        envelope["provider_address"] = self.address
        # The SDK nests these under "response"; some marketplaces read them top-level (Pokter's own
        # agents do). The signed hash covers the derived description, not the envelope layout.
        for key in ("negotiated_at", "quote_expires_at"):
            envelope.setdefault(key, envelope.get("response", {}).get(key))
        return envelope

    # ---------------------------------------------------------------- delivery

    def _submit_locked(self, job_id: int, content: str, metadata: dict) -> dict:
        # The SDK signs submit() itself; hold the agent-wide tx lock around it.
        with chain.TX_LOCK:
            return asyncio.run(self.ops.submit_result(job_id, content, metadata))

    async def deliver(self, job_id: int) -> dict:
        check = await self.ops.verify_job(job_id)
        if not check.get("valid"):
            return {"status": "rejected", "job_id": job_id, "reason": check.get("error")}
        description = check["job"].get("description", "")
        try:
            task = json.loads(description).get("task") or description
        except (ValueError, AttributeError):
            task = description
        assessment = await asyncio.to_thread(report.assessment, task, True)  # paid job: add the Claude layer
        assessment["rate_this_agent"] = {
            "url": reputation.rate_link(job_id),
            "how": "Open the link with the wallet you hired from: one transaction writes your score to the ERC-8004 "
                   "Reputation Registry for this agent.",
        }
        result = await asyncio.to_thread(
            self._submit_locked, job_id, json.dumps(assessment, ensure_ascii=False),
            {"agent": settings.AGENT_NAME, "answer": assessment["answer"]},
        )
        if not result.get("success"):
            log.error("submit %s failed: %s", job_id, result)
            return {"status": "failed", "job_id": job_id, "reason": result.get("error")}
        log.info("delivered job %s: %s/tx/%s", job_id, settings.EXPLORER, result["txHash"])
        return {"status": "submitted", "job_id": job_id, "tx": result["txHash"],
                "deliverable_url": result["deliverableUrl"], "answer": assessment["answer"]}

    # ------------------------------------------------------------------- polls

    async def deliver_pending(self) -> list[dict]:
        pending = await self.ops.get_pending_jobs()
        return [await self.deliver(int(job["jobId"])) for job in pending.get("jobs", [])]

    async def settle_due(self) -> list[dict]:
        window = int(await asyncio.to_thread(self.client.policy.dispute_window))
        submitted = await self.ops.get_submitted_jobs()
        settled = []
        for job in submitted.get("jobs", []):
            if time.time() <= int(job["submittedAt"]) + window + SETTLE_GRACE_SECONDS:
                continue
            with chain.TX_LOCK:
                tx = (await asyncio.to_thread(self.client.settle, int(job["jobId"])))["transactionHash"]
            log.info("settled job %s: %s/tx/%s", job["jobId"], settings.EXPLORER, tx)
            settled.append({"job_id": int(job["jobId"]), "tx": tx})
        return settled
