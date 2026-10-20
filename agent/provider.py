"""ERC-8183 seller side: signed quotes, funded-job delivery and settlement.

Flow (BSC testnet, BNB's AgenticCommerce kernel; Pokter and other escrow marketplaces fund it):
negotiate -> buyer funds escrow -> notify_funded (or the background poll) -> verify the job is
ours, FUNDED and carries a quote we signed -> build the assessment -> submit its manifest hash ->
after the dispute window, settle so the job ends COMPLETED.
"""

import asyncio
import json
import logging
import time

from bnbagent import EVMWalletProvider
from bnbagent.erc8183 import ERC8183JobOps, JobStatus, NegotiationHandler
from bnbagent.storage import LocalStorageProvider

from . import chain, report, settings, state

log = logging.getLogger("halong.provider")

DEFAULT_TERMS = {
    "deliverables": "A JSON yield assessment with recommendation, expected earnings, risks and data sources.",
    "quality_standards": "Read-only analysis from live Venus mainnet data. Execute no transaction for the buyer.",
}
SETTLE_GRACE_SECONDS = 30


def _storage():
    if settings.STORAGE_API_KEY:
        from bnbagent.storage import IPFSStorageProvider
        return IPFSStorageProvider(api_key=settings.STORAGE_API_KEY)
    return LocalStorageProvider(str(settings.DATA_DIR / "deliverables"))


class Provider:
    def __init__(self):
        wallet = EVMWalletProvider(password=settings.WALLET_PASSWORD, private_key=settings.require_agent_key())
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
        self._inflight: set[int] = set()

    # ------------------------------------------------------------------ quotes

    def negotiate(self, data: dict) -> dict:
        task = (data.get("task_description") or data.get("task") or "").strip()
        terms = data.get("terms") if isinstance(data.get("terms"), dict) else DEFAULT_TERMS
        if not task or not report.in_scope(task):
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
        if job_id in self._inflight:
            return {"status": "in_progress", "job_id": job_id}
        self._inflight.add(job_id)
        try:
            check = await self.ops.verify_job(job_id)
            if not check.get("valid"):
                return {"status": "rejected", "job_id": job_id, "reason": check.get("error")}
            description = check.get("job", {}).get("description") or (await self.ops.get_job(job_id)).get("description", "")
            try:
                task = json.loads(description).get("task") or description
            except (ValueError, AttributeError):
                task = description
            assessment = await asyncio.to_thread(report.assessment, task)
            result = await asyncio.to_thread(
                self._submit_locked, job_id, json.dumps(assessment, ensure_ascii=False),
                {"agent": settings.AGENT_NAME, "answer": assessment["answer"]},
            )
            if not result.get("success"):
                log.error("submit %s failed: %s", job_id, result)
                return {"status": "failed", "job_id": job_id, "reason": result.get("error")}
            state.update(lambda s: s["submitted_jobs"].__setitem__(
                str(job_id), {"tx": result["txHash"], "url": result["deliverableUrl"], "at": int(time.time())}))
            state.log_action("deliver", f"delivered ERC-8183 job {job_id}: {assessment['answer'][:120]}",
                             result["txHash"])
            return {"status": "submitted", "job_id": job_id, "tx": result["txHash"],
                    "deliverable_url": result["deliverableUrl"], "answer": assessment["answer"]}
        finally:
            self._inflight.discard(job_id)

    # -------------------------------------------------------------- background

    async def tick(self) -> None:
        pending = await self.ops.get_pending_jobs()
        for job in pending.get("jobs", []):
            outcome = await self.deliver(int(job["jobId"]))
            log.info("job %s: %s", job["jobId"], outcome.get("status"))
        await asyncio.to_thread(self._settle_due)

    def _settle_due(self) -> None:
        window = int(self.client.policy.dispute_window())
        for key, info in list(state.get("submitted_jobs").items()):
            if info.get("final"):
                continue
            job = self.client.get_job(int(key))
            if job.status == JobStatus.SUBMITTED and time.time() > job.submitted_at + window + SETTLE_GRACE_SECONDS:
                with chain.TX_LOCK:
                    tx = self.client.settle(int(key))["transactionHash"]
                state.log_action("settle", f"settled ERC-8183 job {key} after the dispute window", tx)
                job = self.client.get_job(int(key))
            if job.status in (JobStatus.COMPLETED, JobStatus.REJECTED, JobStatus.EXPIRED):
                state.update(lambda s: s["submitted_jobs"][key].__setitem__("final", job.status.name))
