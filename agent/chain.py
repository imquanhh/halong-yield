"""Web3 connections and the agent wallet's transaction sender.

Every transaction the agent wallet signs (treasury moves, job submissions, settlements,
HelloFugu claims) goes through TX_LOCK, so only one is ever in flight, and takes its nonce
from bnbagent's NonceManager, the same counter the SDK's own submit/settle calls use.
"""

import threading
from functools import lru_cache

from bnbagent.core import NonceManager
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from . import settings

TX_LOCK = threading.RLock()


def _connect(rpc: str) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)  # BSC headers carry POA extraData
    return w3


@lru_cache(maxsize=1)
def testnet() -> Web3:
    return _connect(settings.TESTNET_RPC)


@lru_cache(maxsize=1)
def mainnet() -> Web3:
    return _connect(settings.MAINNET_RPC)


@lru_cache(maxsize=1)
def agent_account() -> LocalAccount:
    return Account.from_key(settings.require_agent_key())


def send(fn=None, *, tx: dict | None = None, value: int = 0) -> str:
    """Sign and broadcast from the agent wallet, wait for the receipt, return the tx hash.

    Pass a contract function (`contract.functions.x(...)`) or a raw `tx` dict.
    Raises if the transaction reverts.
    """
    w3, acct = testnet(), agent_account()
    nonces = NonceManager.for_account(w3, acct.address)
    with TX_LOCK:
        if fn is not None:
            tx = fn.build_transaction({"from": acct.address, "value": value})
        tx = {k: v for k, v in tx.items()
              if k not in ("gas", "gasPrice", "maxFeePerGas", "maxPriorityFeePerGas", "nonce")}
        tx |= {"from": acct.address, "chainId": settings.CHAIN_ID, "gasPrice": w3.eth.gas_price}
        tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.25)
        nonce = nonces.reserve()
        try:
            signed = acct.sign_transaction({**tx, "nonce": nonce})
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        except Exception as exc:
            if not nonces.handle_error(exc, nonce):
                nonces.release(nonce)
            raise
        nonces.mark_broadcast(nonce, tx_hash.to_0x_hex())
        try:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        finally:
            nonces.mark_finalized(nonce)
    if receipt["status"] != 1:
        raise RuntimeError(f"transaction reverted: {settings.EXPLORER}/tx/{tx_hash.to_0x_hex()}")
    return tx_hash.to_0x_hex()
