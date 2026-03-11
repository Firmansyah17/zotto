"""
╔══════════════════════════════════════════════════════╗
║       ZOTTO AUTO SWAP BOT — Neura Testnet            ║
║  Otomatis swap ANKR ↔ USDT                          ║
╚══════════════════════════════════════════════════════╝
"""

import os
import time
import json
from web3 import Web3
from eth_abi import encode
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

RPC_URL  = "https://testnet.rpc.neuraprotocol.io"
CHAIN_ID = 267

ZOTTO_ROUTER = "0x6836F8A9a66ab8430224aa9b4E6D24dc8d7d5d77"
USDT_TOKEN   = "0x3A631ee99eF7fE2D248116982b14e7615ac77502"
WANKR_TOKEN  = "0x422F5Eae5fEE0227FB31F149E690a73C4aD02dB8"

SWAP_AMOUNT_ANKR = 55
DELAY_ANTAR_SWAP = 15
TARGET_VOLUME_USD = 1000
HARGA_ANKR_USD   = 0.004338

# Selectors dari raw trace
SEL_EXACT_INPUT = bytes.fromhex("1679c792")
SEL_UNWRAP      = bytes.fromhex("69bc35b2")
SEL_MULTICALL   = bytes.fromhex("ac9650d8")

MULTICALL_ABI = json.loads('''[
  {
    "name": "multicall",
    "type": "function",
    "stateMutability": "payable",
    "inputs": [{"name": "data", "type": "bytes[]"}],
    "outputs": [{"name": "results", "type": "bytes[]"}]
  }
]''')

ERC20_ABI = json.loads('''[
  {"name": "approve",   "type": "function",
   "inputs": [{"name": "spender","type": "address"},{"name": "amount","type": "uint256"}],
   "outputs": [{"name": "","type": "bool"}]},
  {"name": "balanceOf", "type": "function", "stateMutability": "view",
   "inputs": [{"name": "account","type": "address"}],
   "outputs": [{"name": "","type": "uint256"}]},
  {"name": "allowance", "type": "function", "stateMutability": "view",
   "inputs": [{"name": "owner","type": "address"},{"name": "spender","type": "address"}],
   "outputs": [{"name": "","type": "uint256"}]}
]''')

# WANKR punya deposit() untuk wrap ANKR native → WANKR
WANKR_ABI = json.loads('''[
  {"name": "deposit",   "type": "function", "stateMutability": "payable",
   "inputs": [], "outputs": []},
  {"name": "withdraw",  "type": "function",
   "inputs": [{"name": "wad","type": "uint256"}], "outputs": []},
  {"name": "approve",   "type": "function",
   "inputs": [{"name": "spender","type": "address"},{"name": "amount","type": "uint256"}],
   "outputs": [{"name": "","type": "bool"}]},
  {"name": "balanceOf", "type": "function", "stateMutability": "view",
   "inputs": [{"name": "account","type": "address"}],
   "outputs": [{"name": "","type": "uint256"}]},
  {"name": "allowance", "type": "function", "stateMutability": "view",
   "inputs": [{"name": "owner","type": "address"},{"name": "spender","type": "address"}],
   "outputs": [{"name": "","type": "uint256"}]}
]''')

w3 = Web3(Web3.HTTPProvider(RPC_URL))

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
if not PRIVATE_KEY:
    raise ValueError("❌ PRIVATE_KEY tidak ditemukan di file .env!")

account = w3.eth.account.from_key(PRIVATE_KEY)
WALLET  = account.address

router = w3.eth.contract(address=Web3.to_checksum_address(ZOTTO_ROUTER), abi=MULTICALL_ABI)
usdt   = w3.eth.contract(address=Web3.to_checksum_address(USDT_TOKEN),   abi=ERC20_ABI)
wankr  = w3.eth.contract(address=Web3.to_checksum_address(WANKR_TOKEN),  abi=WANKR_ABI)

ZERO_ADDR = "0x0000000000000000000000000000000000000000"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def cek_balance():
    ankr_wei  = w3.eth.get_balance(WALLET)
    usdt_wei  = usdt.functions.balanceOf(WALLET).call()
    wankr_wei = wankr.functions.balanceOf(WALLET).call()
    ankr_bal  = float(w3.from_wei(ankr_wei, "ether"))
    usdt_bal  = float(usdt_wei) / 1e6
    wankr_bal = float(w3.from_wei(wankr_wei, "ether"))
    log(f"💰 ANKR: {ankr_bal:.4f} | WANKR: {wankr_bal:.4f} | USDT: {usdt_bal:.4f}")
    return ankr_bal, wankr_bal, usdt_bal

def kirim_tx(tx_built):
    nonce = w3.eth.get_transaction_count(WALLET)
    tx_built["nonce"] = nonce
    signed  = w3.eth.account.sign_transaction(tx_built, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return receipt.status == 1, tx_hash.hex()

def approve_jika_perlu(token_contract, spender, amount_wei):
    allowance = token_contract.functions.allowance(WALLET, spender).call()
    if allowance >= amount_wei:
        return True
    log(f"🔓 Approving token ke router...")
    tx = token_contract.functions.approve(
        Web3.to_checksum_address(spender), 2**256 - 1
    ).build_transaction({
        "chainId": CHAIN_ID, "gas": 100000,
        "gasPrice": w3.eth.gas_price, "from": WALLET,
    })
    ok, txh = kirim_tx(tx)
    log(f"{'✅' if ok else '❌'} Approve: {txh[:16]}...")
    return ok

def encode_exact_input_single(token_in, token_out, recipient, deadline, amount_in, amount_out_min=0):
    params = encode(
        ["(address,address,uint24,address,uint256,uint256,uint256,uint160)"],
        [(
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out),
            0,  # fee = 0
            Web3.to_checksum_address(recipient),
            deadline,
            amount_in,
            amount_out_min,
            0   # sqrtPriceLimitX96
        )]
    )
    return SEL_EXACT_INPUT + params

def encode_unwrap_weth9(amount_min, recipient):
    params = encode(["uint256","address"], [amount_min, Web3.to_checksum_address(recipient)])
    return SEL_UNWRAP + params

# ════════════════════════════════════════════
# SWAP FUNCTIONS
# Pendekatan baru: wrap dulu manual, baru swap token→token
# (sama persis dengan pola tx yang berhasil)
# ════════════════════════════════════════════

def wrap_ankr(ankr_amount):
    """ANKR native → WANKR via deposit()"""
    amount_wei = w3.to_wei(ankr_amount, "ether")
    log(f"📦 Wrapping {ankr_amount} ANKR → WANKR...")
    tx = wankr.functions.deposit().build_transaction({
        "chainId": CHAIN_ID,
        "value":   amount_wei,
        "gas":     60000,
        "gasPrice": w3.eth.gas_price,
        "from": WALLET,
    })
    ok, txh = kirim_tx(tx)
    log(f"{'✅' if ok else '❌'} Wrap: {txh[:16]}...")
    return ok

def swap_wankr_ke_usdt(wankr_amount_wei):
    """WANKR (token) → USDT — sama persis pola tx berhasil"""
    approve_jika_perlu(wankr, ZOTTO_ROUTER, wankr_amount_wei)
    deadline = int(time.time()) + 300

    # recipient = WALLET langsung (tidak perlu unwrap karena dapat USDT)
    swap_data = encode_exact_input_single(
        WANKR_TOKEN, USDT_TOKEN, WALLET, deadline, wankr_amount_wei
    )

    tx = router.functions.multicall([swap_data]).build_transaction({
        "chainId": CHAIN_ID, "value": 0,
        "gas": 300000, "gasPrice": w3.eth.gas_price, "from": WALLET,
    })
    ok, txh = kirim_tx(tx)
    log(f"{'✅' if ok else '❌'} WANKR→USDT | tx: {txh[:16]}...")
    return ok

def swap_usdt_ke_ankr():
    """USDT → WANKR → ANKR native — identik dengan tx berhasil"""
    usdt_balance = usdt.functions.balanceOf(WALLET).call()
    if usdt_balance == 0:
        log("⚠️  Tidak ada USDT")
        return False

    approve_jika_perlu(usdt, ZOTTO_ROUTER, usdt_balance)
    deadline = int(time.time()) + 300

    # recipient = 0x0 → router simpan WANKR dulu, lalu unwrap
    swap_data   = encode_exact_input_single(USDT_TOKEN, WANKR_TOKEN, ZERO_ADDR, deadline, usdt_balance)
    unwrap_data = encode_unwrap_weth9(0, WALLET)

    tx = router.functions.multicall([swap_data, unwrap_data]).build_transaction({
        "chainId": CHAIN_ID, "value": 0,
        "gas": 350000, "gasPrice": w3.eth.gas_price, "from": WALLET,
    })
    ok, txh = kirim_tx(tx)
    log(f"{'✅' if ok else '❌'} USDT→ANKR | tx: {txh[:16]}...")
    return ok

def update_progress(swap_ke, volume_terkumpul, target):
    persen = min(100, (volume_terkumpul / target) * 100)
    with open("progress.md", "w") as f:
        f.write(f"""# 🤖 Zotto Bot Progress
**Wallet:** `{WALLET}`
**Last Update:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

| Target | Terkumpul | Progress |
|--------|-----------|----------|
| ${target:,.0f} | ${volume_terkumpul:,.4f} | {persen:.1f}% |

- Total swap: **{swap_ke}x**
""")

def main():
    print("╔══════════════════════════════════════╗")
    print("║     ZOTTO AUTO SWAP BOT — Mulai!     ║")
    print("╚══════════════════════════════════════╝\n")

    if not w3.is_connected():
        log("❌ Gagal konek ke Neura Testnet."); return

    log(f"✅ Terhubung ke Neura Testnet (Chain ID: {CHAIN_ID})")
    log(f"👛 Wallet: {WALLET}\n")

    ankr_bal, _, _ = cek_balance()
    if ankr_bal < SWAP_AMOUNT_ANKR + 2:
        log(f"❌ Balance tidak cukup. Perlu minimal {SWAP_AMOUNT_ANKR + 2} ANKR"); return

    # Estimasi berapa putaran yang bisa dilakukan dengan balance sekarang
    GAS_PER_PUTARAN = 0.15  # estimasi gas ANKR per putaran (wrap + swap + unwrap)
    ANKR_CADANGAN   = 5     # sisakan 5 ANKR untuk keamanan
    putaran_bisa    = int((ankr_bal - ANKR_CADANGAN) / (SWAP_AMOUNT_ANKR + GAS_PER_PUTARAN))
    volume_maks     = putaran_bisa * SWAP_AMOUNT_ANKR * HARGA_ANKR_USD * 2

    volume_per_putaran = SWAP_AMOUNT_ANKR * HARGA_ANKR_USD * 2
    putaran_target     = int(TARGET_VOLUME_USD / volume_per_putaran) + 1

    log(f"📋 Target volume   : ${TARGET_VOLUME_USD:,}")
    log(f"🔄 Putaran dibutuhkan: {putaran_target:,}x")
    log(f"💡 Dengan balance sekarang, bisa {putaran_bisa}x putaran (~${volume_maks:.2f})")

    if putaran_bisa < putaran_target:
        log(f"\n⚠️  PERHATIAN: ANKR tidak cukup untuk capai target ${TARGET_VOLUME_USD:,}!")
        log(f"   Estimasi ANKR dibutuhkan: {putaran_target * (SWAP_AMOUNT_ANKR + GAS_PER_PUTARAN):.0f} ANKR")
        log(f"   Bot akan jalan sampai ANKR habis, lalu berhenti otomatis.")
        log(f"   Setelah claim faucet, jalankan ulang bot untuk lanjutkan.\n")
        time.sleep(3)
    else:
        log(f"✅ Balance cukup untuk capai target!\n")

    volume_terkumpul = 0.0
    swap_ke = 0
    putaran_aktual = min(putaran_bisa, putaran_target)

    for i in range(putaran_aktual):
        log(f"── Putaran {i+1}/{putaran_aktual} ──")

        # Cek balance sebelum mulai putaran
        ankr_bal, _, _ = cek_balance()

        # Peringatan 10 putaran sebelum habis
        sisa_putaran = int((ankr_bal - ANKR_CADANGAN) / (SWAP_AMOUNT_ANKR + GAS_PER_PUTARAN))
        if sisa_putaran <= 10:
            log(f"⚠️  PERINGATAN: ANKR hampir habis! Estimasi sisa {sisa_putaran} putaran lagi.")

        if ankr_bal < SWAP_AMOUNT_ANKR + 1:
            log("🛑 ANKR habis! Bot berhenti.")
            log(f"   Claim faucet di Neuraverse, lalu jalankan ulang bot.")
            log(f"   Progress terakhir: ${volume_terkumpul:.4f} / ${TARGET_VOLUME_USD:,}")
            break

        # Step 1: wrap ANKR → WANKR
        if not wrap_ankr(SWAP_AMOUNT_ANKR):
            log("❌ Wrap gagal, skip putaran ini")
            time.sleep(DELAY_ANTAR_SWAP); continue

        time.sleep(5)

        # Step 2: WANKR → USDT
        wankr_balance = wankr.functions.balanceOf(WALLET).call()
        if swap_wankr_ke_usdt(wankr_balance):
            volume_terkumpul += SWAP_AMOUNT_ANKR * HARGA_ANKR_USD
            swap_ke += 1
            time.sleep(DELAY_ANTAR_SWAP)

            # Step 3: USDT → ANKR
            if swap_usdt_ke_ankr():
                volume_terkumpul += SWAP_AMOUNT_ANKR * HARGA_ANKR_USD
                swap_ke += 1

        persen = min(100, (volume_terkumpul / TARGET_VOLUME_USD) * 100)
        log(f"📊 Volume: ${volume_terkumpul:.4f} / ${TARGET_VOLUME_USD:,} ({persen:.1f}%)")
        update_progress(swap_ke, volume_terkumpul, TARGET_VOLUME_USD)

        if volume_terkumpul >= TARGET_VOLUME_USD:
            log(f"\n🎉 TARGET ${TARGET_VOLUME_USD:,} TERCAPAI! Total swap: {swap_ke}x")
            log(f"   Sekarang update Karma dengan link tx terakhir kamu!")
            break

        time.sleep(DELAY_ANTAR_SWAP)

    log(f"\n✅ Bot selesai! Volume terkumpul: ${volume_terkumpul:.4f}")
    log(f"   Total swap berhasil: {swap_ke}x")
    if volume_terkumpul < TARGET_VOLUME_USD:
        sisa = TARGET_VOLUME_USD - volume_terkumpul
        log(f"   Sisa yang dibutuhkan: ${sisa:.4f} — claim faucet dan jalankan ulang!")

if __name__ == "__main__":
    main()
