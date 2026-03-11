"""
╔══════════════════════════════════════════════════════╗
║       ZOTTO AUTO SWAP BOT — Neura Testnet            ║
║  Otomatis swap ANKR ↔ MOLLY buat farming volume      ║
╚══════════════════════════════════════════════════════╝

CARA PAKAI:
1. Install dependency:  pip install web3 python-dotenv
2. Isi file .env dengan PRIVATE_KEY kamu
3. Isi CONTRACT ADDRESSES di bawah (lihat SETUP_BOT.md)
4. Jalankan: python zotto_bot.py
"""

import os
import time
import json
from web3 import Web3
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# ════════════════════════════════════════════
# ⚙️  KONFIGURASI — EDIT BAGIAN INI
# ════════════════════════════════════════════

# Neura Testnet
RPC_URL = "https://testnet.rpc.neuraprotocol.io"
CHAIN_ID = 267

# Contract addresses — isi sesuai SETUP_BOT.md
ZOTTO_ROUTER   = "0x6836F8A9a66ab8430224aa9b4E6D24dc8d7d5d77"   # Zotto Router
MOLLY_TOKEN    = "0x3A631ee99eF7fE2D248116982b14e7615ac77502"    # Token MOLLY
WANKR_TOKEN    = "0x422F5Eae5fEE0227FB31F149E690a73C4aD02dB8"    # Wrapped ANKR (WANKR)

# Pengaturan swap
SWAP_AMOUNT_ANKR = 55        # Jumlah ANKR per swap (sesuaikan dengan balance kamu)
DELAY_ANTAR_SWAP = 15         # Jeda antar swap dalam detik (jangan terlalu cepat)
TARGET_VOLUME_USD = 1000      # Target volume dalam USD ($1K atau $10K)
HARGA_ANKR_USD   = 0.04       # Perkiraan harga ANKR/USD (update kalau perlu)

# ════════════════════════════════════════════
# ABI — Uniswap V2 Router (Zotto pakai format ini)
# ════════════════════════════════════════════

ROUTER_ABI = json.loads('''[
  {
    "name": "swapExactETHForTokens",
    "type": "function",
    "inputs": [
      {"name": "amountOutMin", "type": "uint256"},
      {"name": "path", "type": "address[]"},
      {"name": "to", "type": "address"},
      {"name": "deadline", "type": "uint256"}
    ],
    "outputs": [{"name": "amounts", "type": "uint256[]"}]
  },
  {
    "name": "swapExactTokensForETH",
    "type": "function",
    "inputs": [
      {"name": "amountIn", "type": "uint256"},
      {"name": "amountOutMin", "type": "uint256"},
      {"name": "path", "type": "address[]"},
      {"name": "to", "type": "address"},
      {"name": "deadline", "type": "uint256"}
    ],
    "outputs": [{"name": "amounts", "type": "uint256[]"}]
  },
  {
    "name": "getAmountsOut",
    "type": "function",
    "inputs": [
      {"name": "amountIn", "type": "uint256"},
      {"name": "path", "type": "address[]"}
    ],
    "outputs": [{"name": "amounts", "type": "uint256[]"}],
    "stateMutability": "view"
  }
]''')

ERC20_ABI = json.loads('''[
  {
    "name": "approve",
    "type": "function",
    "inputs": [
      {"name": "spender", "type": "address"},
      {"name": "amount", "type": "uint256"}
    ],
    "outputs": [{"name": "", "type": "bool"}]
  },
  {
    "name": "balanceOf",
    "type": "function",
    "inputs": [{"name": "account", "type": "address"}],
    "outputs": [{"name": "", "type": "uint256"}],
    "stateMutability": "view"
  },
  {
    "name": "allowance",
    "type": "function",
    "inputs": [
      {"name": "owner", "type": "address"},
      {"name": "spender", "type": "address"}
    ],
    "outputs": [{"name": "", "type": "uint256"}],
    "stateMutability": "view"
  }
]''')

# ════════════════════════════════════════════
# INISIALISASI
# ════════════════════════════════════════════

w3 = Web3(Web3.HTTPProvider(RPC_URL))

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
if not PRIVATE_KEY:
    raise ValueError("❌ PRIVATE_KEY tidak ditemukan di file .env!")

account = w3.eth.account.from_key(PRIVATE_KEY)
WALLET  = account.address

router = w3.eth.contract(address=Web3.to_checksum_address(ZOTTO_ROUTER), abi=ROUTER_ABI)
molly  = w3.eth.contract(address=Web3.to_checksum_address(MOLLY_TOKEN),  abi=ERC20_ABI)

# ════════════════════════════════════════════
# FUNGSI UTAMA
# ════════════════════════════════════════════

def log(msg):
    """Print dengan timestamp"""
    waktu = datetime.now().strftime("%H:%M:%S")
    print(f"[{waktu}] {msg}")

def cek_balance():
    """Cek balance ANKR dan MOLLY"""
    ankr_wei  = w3.eth.get_balance(WALLET)
    molly_wei = molly.functions.balanceOf(WALLET).call()
    ankr      = w3.from_wei(ankr_wei, "ether")
    molly_bal = w3.from_wei(molly_wei, "ether")
    log(f"💰 Balance — ANKR: {ankr:.4f} | MOLLY: {molly_bal:.4f}")
    return float(ankr), float(molly_bal)

def approve_molly(amount_wei):
    """Approve MOLLY agar bisa dijual ke router"""
    allowance = molly.functions.allowance(WALLET, ZOTTO_ROUTER).call()
    if allowance >= amount_wei:
        return  # Sudah di-approve

    log("🔓 Approve MOLLY ke router...")
    nonce = w3.eth.get_transaction_count(WALLET)
    tx = molly.functions.approve(
        Web3.to_checksum_address(ZOTTO_ROUTER),
        2**256 - 1  # approve max supaya tidak perlu approve lagi
    ).build_transaction({
        "chainId": CHAIN_ID,
        "gas": 100000,
        "gasPrice": w3.eth.gas_price,
        "nonce": nonce,
    })
    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash)
    log(f"✅ Approve berhasil: {tx_hash.hex()}")

def swap_ankr_ke_molly(ankr_amount):
    """Swap ANKR (native) → MOLLY"""
    amount_in_wei = w3.to_wei(ankr_amount, "ether")
    deadline      = int(time.time()) + 300  # 5 menit

    path = [
        Web3.to_checksum_address(WANKR_TOKEN),
        Web3.to_checksum_address(MOLLY_TOKEN)
    ]

    # Hitung estimasi output
    try:
        amounts = router.functions.getAmountsOut(amount_in_wei, path).call()
        min_out = int(amounts[1] * 0.95)  # 5% slippage tolerance
    except:
        min_out = 0

    nonce = w3.eth.get_transaction_count(WALLET)
    tx = router.functions.swapExactETHForTokens(
        min_out,
        path,
        WALLET,
        deadline
    ).build_transaction({
        "chainId": CHAIN_ID,
        "value": amount_in_wei,
        "gas": 200000,
        "gasPrice": w3.eth.gas_price,
        "nonce": nonce,
    })

    signed  = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    status = "✅" if receipt.status == 1 else "❌"
    log(f"{status} ANKR→MOLLY | {ankr_amount} ANKR | tx: {tx_hash.hex()[:16]}...")
    return receipt.status == 1

def swap_molly_ke_ankr():
    """Swap semua MOLLY → ANKR (native)"""
    molly_balance = molly.functions.balanceOf(WALLET).call()
    if molly_balance == 0:
        log("⚠️  Tidak ada MOLLY untuk dijual")
        return False

    approve_molly(molly_balance)

    deadline = int(time.time()) + 300
    path = [
        Web3.to_checksum_address(MOLLY_TOKEN),
        Web3.to_checksum_address(WANKR_TOKEN)
    ]

    try:
        amounts = router.functions.getAmountsOut(molly_balance, path).call()
        min_out = int(amounts[1] * 0.95)
    except:
        min_out = 0

    nonce = w3.eth.get_transaction_count(WALLET)
    tx = router.functions.swapExactTokensForETH(
        molly_balance,
        min_out,
        path,
        WALLET,
        deadline
    ).build_transaction({
        "chainId": CHAIN_ID,
        "gas": 200000,
        "gasPrice": w3.eth.gas_price,
        "nonce": nonce,
    })

    signed  = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    status = "✅" if receipt.status == 1 else "❌"
    log(f"{status} MOLLY→ANKR | tx: {tx_hash.hex()[:16]}...")
    return receipt.status == 1

def update_progress(swap_ke, volume_terkumpul, target):
    """Update file progress.md"""
    persen  = min(100, (volume_terkumpul / target) * 100)
    isi = f"""# 🤖 Zotto Bot Progress

**Wallet:** `{WALLET}`
**Last Update:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## 📊 Volume Progress

| Target | Terkumpul | Progress |
|--------|-----------|----------|
| ${target:,.0f} | ${volume_terkumpul:,.2f} | {persen:.1f}% |

## 📈 Statistik

- Total swap selesai: **{swap_ke}x**
- Volume per swap: **~${SWAP_AMOUNT_ANKR * HARGA_ANKR_USD * 2:.2f}**

## 📝 Log Terakhir

Swap terakhir: {datetime.now().strftime("%H:%M:%S")}
"""
    with open("progress.md", "w") as f:
        f.write(isi)

# ════════════════════════════════════════════
# MAIN — LOOP UTAMA
# ════════════════════════════════════════════

def main():
    print("╔══════════════════════════════════════╗")
    print("║     ZOTTO AUTO SWAP BOT — Mulai!     ║")
    print("╚══════════════════════════════════════╝\n")

    # Validasi koneksi
    if not w3.is_connected():
        log("❌ Gagal konek ke Neura Testnet. Cek RPC URL.")
        return

    log(f"✅ Terhubung ke Neura Testnet (Chain ID: {CHAIN_ID})")
    log(f"👛 Wallet: {WALLET}\n")

    ankr_bal, _ = cek_balance()
    if ankr_bal < SWAP_AMOUNT_ANKR * 2:
        log(f"❌ Balance ANKR tidak cukup! Minimal butuh {SWAP_AMOUNT_ANKR * 2} ANKR")
        return

    # Hitung berapa swap yang dibutuhkan
    volume_per_putaran = SWAP_AMOUNT_ANKR * HARGA_ANKR_USD * 2  # ANKR→MOLLY + MOLLY→ANKR
    putaran_dibutuhkan = int(TARGET_VOLUME_USD / volume_per_putaran) + 1
    log(f"📋 Target: ${TARGET_VOLUME_USD:,} | Estimasi putaran: {putaran_dibutuhkan}x\n")

    volume_terkumpul = 0
    swap_ke = 0

    for i in range(putaran_dibutuhkan):
        log(f"── Putaran {i+1}/{putaran_dibutuhkan} ──")

        # Swap 1: ANKR → MOLLY
        if swap_ankr_ke_molly(SWAP_AMOUNT_ANKR):
            volume_terkumpul += SWAP_AMOUNT_ANKR * HARGA_ANKR_USD
            swap_ke += 1
            time.sleep(DELAY_ANTAR_SWAP)

            # Swap 2: MOLLY → ANKR
            if swap_molly_ke_ankr():
                volume_terkumpul += SWAP_AMOUNT_ANKR * HARGA_ANKR_USD
                swap_ke += 1

        log(f"📊 Volume terkumpul: ${volume_terkumpul:.2f} / ${TARGET_VOLUME_USD:,}")
        update_progress(swap_ke, volume_terkumpul, TARGET_VOLUME_USD)

        if volume_terkumpul >= TARGET_VOLUME_USD:
            log(f"\n🎉 TARGET TERCAPAI! Total swap: {swap_ke}x")
            break

        # Cek balance masih cukup
        ankr_bal, _ = cek_balance()
        if ankr_bal < SWAP_AMOUNT_ANKR:
            log("⚠️  Balance ANKR hampir habis, bot berhenti.")
            break

        time.sleep(DELAY_ANTAR_SWAP)

    log("\n✅ Bot selesai! Cek progress.md untuk detail.")

if __name__ == "__main__":
    main()
