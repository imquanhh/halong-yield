"""Hire track for Set and Earn, on BSC testnet.

    python -m hire check        wallet, balances, what is missing
    python -m hire plan         price every planned hire; signs nothing
    python -m hire run --yes    execute the planned hires that are not done yet
    python -m hire status       every hire this wallet has made, checked against the rule

Requirement: >= 3 hires, each a different agent, across >= 2 shortlisted marketplaces.
Hires are never cancelled: a hire placed and unwound does not count.
"""

import argparse
import sys

from web3 import Web3

from . import erc8183, hellofugu
from .config import FAUCET, campaign_account, connect, log_hire

HIRE_DURATION_S = 3600

# HelloFugu listings to hire. Already hired elsewhere from this wallet, so not planned here:
# 97:2623 Nagi (Pokter job 1565) and 97:2556 Venus Rate Router (Pokter job 1566).
PLAN = [
    23,  # agent 97:2554, yield, third-party
    21,  # agent 97:2545, rebalancing, third-party
]
MIN_TBNB = Web3.to_wei(0.005, "ether")


def _describe(p):
    cost = f"{Web3.from_wei(p['amountWei'], 'ether'):.6f} tBNB (${p['usd']:.2f}, {p['durationSeconds'] // 60} phút)"
    flag = "ĐÃ HIRE" if p["alreadyHired"] else "chưa"
    return f"HelloFugu  {p['agent']:<9} listing {p['listingId']:<3} {p['category']:<14} {cost:<36} [{flag}]"


def cmd_check(w3, acct):
    bnb = w3.eth.get_balance(acct.address)
    print(f"Ví campaign : {acct.address}")
    print(f"tBNB        : {Web3.from_wei(bnb, 'ether'):.6f}")
    if bnb < MIN_TBNB:
        print(f"\n! Cần ít nhất {Web3.from_wei(MIN_TBNB, 'ether')} tBNB cho phí gas và tiền hire HelloFugu.")
        print(f"  Lấy tBNB miễn phí tại {FAUCET}")
    else:
        print("\nĐủ tBNB để chạy hire.")


def cmd_plan(w3, acct):
    plans = [hellofugu.prepare(w3, acct.address, lid, HIRE_DURATION_S) for lid in PLAN]
    for p in plans:
        print(_describe(p))
    need = sum(p["amountWei"] for p in plans if not p["alreadyHired"])
    print(f"\nTổng tiền hire: {Web3.from_wei(need, 'ether'):.6f} tBNB (chưa tính gas).")


def cmd_run(w3, acct):
    if w3.eth.get_balance(acct.address) < MIN_TBNB:
        sys.exit(f"Không đủ tBNB. Lấy tại {FAUCET} rồi chạy lại.")
    if w3.eth.get_transaction_count(acct.address, "pending") != w3.eth.get_transaction_count(acct.address):
        sys.exit("Ví đang có giao dịch chờ xác nhận. Đợi nó xong rồi chạy lại.")
    failures = 0
    for lid in PLAN:
        p = hellofugu.prepare(w3, acct.address, lid, HIRE_DURATION_S)
        print(_describe(p))
        if p["alreadyHired"]:
            continue
        try:
            result = hellofugu.hire(w3, acct, p)
        except Exception as exc:
            failures += 1
            print(f"  ✗ Lỗi: {exc}")
            continue
        log_hire(result | {"wallet": acct.address})
        print(f"  ✓ Đã hire · sub {result['subId']} · {result['explorer']}")
    print("\nXong." if not failures else f"\n{failures} hire lỗi, chạy lại lệnh để thử lại các hire còn thiếu.")


def cmd_status(w3, acct):
    hires = []  # (marketplace, agent key, label)
    for h in hellofugu.tracked_hires(acct.address):
        hires.append(("HelloFugu", h["agentKey"], f"{h['agentName']} · sub {h['subId']} · {h['status']}"))
    print("Đang quét các job ERC-8183 của ví (mất khoảng 1 phút)...")
    pokter = erc8183.pokter_agents()
    for job in erc8183.jobs_by_client(w3, acct.address):
        agent = pokter.get(job["jobId"])
        market = "Pokter" if agent else "ERC-8183 (không rõ marketplace)"
        key = f"97:{agent['tokenId']}" if agent else job["provider"]
        label = f"{agent['name'] if agent else job['provider']} · job {job['jobId']} · {job['status']}"
        if job["status"] not in erc8183.FUNDED_STATUSES:
            print(f"  (không tính) {market}: {label}")
            continue
        hires.append((market, key, label))
    print()
    for market, key, label in hires:
        print(f"  ✓ {market:<11} {key:<9} {label}")
    distinct = len({key for _, key, _ in hires})
    markets = len({m for m, _, _ in hires if not m.startswith("ERC-8183")})
    ok = distinct >= 3 and markets >= 2
    print(f"\nAgent khác nhau: {distinct} · marketplace: {markets} → {'ĐẠT' if ok else 'CHƯA ĐẠT'} Hire track")


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m hire")
    parser.add_argument("command", choices=["check", "plan", "run", "status"])
    parser.add_argument("--yes", action="store_true", help="bắt buộc với run: xác nhận gửi giao dịch")
    args = parser.parse_args()
    if args.command == "run" and not args.yes:
        sys.exit("run sẽ gửi giao dịch thật trên testnet. Thêm --yes để xác nhận.")
    w3 = connect()
    acct = campaign_account()
    {"check": cmd_check, "plan": cmd_plan, "run": cmd_run, "status": cmd_status}[args.command](w3, acct)


if __name__ == "__main__":
    main()
