"""
╔══════════════════════════════════════════════════════╗
║       ZOTTO AUTO SWAP BOT — Neura Testnet            ║
║  Otomatis swap ANKR ↔ USDT (Uniswap V3 style)       ║
╚══════════════════════════════════════════════════════╝
"""

import os
import time
import json
from web3 import Web3
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# ════════════════════════════════════════════
# ⚙️  KONFIGURASI
# ════════════════════════════════════════════

RPC_URL  = "https://testnet.rpc.neuraprotocol.io"
CHAIN_ID = 267

# Contract addresses (sudah diverifikasi dari explorer)
ZOTTO_ROUTER = "0x6836F8A9a66ab8430224aa9b4E6D24dc8d7d5d77"
USDT_TOKEN   = "0x3A631ee99eF7fE2D248116982b14e7615ac77502"  # Tether USD
WANKR_TOKEN  = "0x422F5Eae5fEE0227FB31F149E690a73C4aD02dB8"  # Wrapped ANKR

# Fee tier — coba urutan ini kalau gagal: 3000 → 500 → 10000
FEE_TIER = 3000

# Pengaturan swap
SWAP_AMOUNT_ANKR = 55       # Jumlah ANKR per swap
DELAY_ANTAR_SWAP = 15       # Jeda antar swap (detik)
TARGET_VOLUME_USD = 1000    # Target volume USD
HARGA_ANKR_USD   = 0.004338 # Harga ANKR saat ini

# ════════════════════════════════════════════
# ABI
# ════════════════════════════════════════════

ROUTER_ABI = json.loads('''[
  {
    "name": "multicall",
    "type": "function",
    "stateMutability": "payable",
    "inputs": [
      {"name": "deadline", "type": "uint256"},
      {"name": "data",     "type": "bytes[]"}
    ],
    "outputs": [{"name": "results", "type": "bytes[]"}]
  },
  {
    "name": "exactInputSingle",
    "type": "function",
    "stateMutability": "payable",
    "inputs": [{
      "name": "params",
      "type": "tuple",
      "components": [
        {"name": "tokenIn",           "type": "address"},
        {"name": "tokenOut",          "type": "address"},
        {"name": "fee",               "type": "uint24"},
        {"name": "recipient",         "type": "address"},
        {"name": "amountIn",          "type": "uint256"},
        {"name": "amountOutMinimum",  "type": "uint256"},
        {"name": "sqrtPriceLimitX96", "type": "uint160"}
      ]
    }],
    "outputs": [{"name": "amountOut", "type": "uint256"}]
  },
  {
    "name": "unwrapWETH9",
    "type": "function",
    "stateMutability": "payable",
    "inputs": [
      {"name": "amountMinimum", "type": "uint256"},
      {"name": "recipient",     "type": "address"}
    ],
    "outputs": []
  },
  {
    "name": "refundETH",
    "type": "function",
    "stateMutability": "payable",
    "inputs":  [],
    "outputs": []
  }
]''')

ERC20_ABI = json.loads('''[
  {
    "name": "approve",
    "type": "function",
    "inputs": [
      {"name": "spender", "type": "address"},
      {"name": "amount",  "type": "uint256"}
    ],
    "outputs": [{"name": "", "type": "bool"}]
  },
  {
    "name": "balanceOf",
    "type": "function",
    "stateMutability": "view",
    "inputs":  [{"name": "account", "type": "address"}],
    "outputs": [{"name": "",        "type": "uint256"}]
  },
  {
    "name": "allowance",
    "type": "function",
    "stateMutability": "view",
    "inputs": [
      {"name": "owner",   "type": "address"},
      {"name": "spender", "type": "address"}
    ],
    "outputs": [{"name": "", "type": "uint256"}]
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
usdt   = w3.eth.contract(address=Web3.to_checksum_address(USDT_TOKEN),   abi=ERC20_ABI)

# ════════════════════════════════════════════
# HELPER
# ════════════════════════════════════════════

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def cek_balance():
    ankr_wei = w3.eth.get_balance(WALLET)
    usdt_wei = usdt.functions.balanceOf(WALLET).call()
    ankr_bal = float(w3.from_wei(ankr_wei, "ether"))
    usdt_bal = float(usdt_wei) / 1e6  # USDT decimals = 6
    log(f"💰 Balance — ANKR: {ankr_bal:.4f} | USDT: {usdt_bal:.4f}")
    return ankr_bal, usdt_bal

def kirim_tx(tx_built):
    nonce = w3.eth.get_transaction_count(WALLET)
    tx_built["nonce"] = nonce
    signed  = w3.eth.account.sign_transaction(tx_built, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return receipt.status == 1, tx_hash.hex()

def approve_token(token_contract, spender, amount_wei):
    allowance = token_contract.functions.allowance(WALLET, spender).call()
    if allowance >= amount_wei:
        return
    log("🔓 Approving USDT...")
    tx = token_contract.functions.approve(
        Web3.to_checksum_address(spender),
        2**256 - 1
    ).build_transaction({
        "chainId":  CHAIN_ID,
        "gas":      100000,
        "gasPrice": w3.eth.gas_price,
        "from":     WALLET,
    })
    ok, txh = kirim_tx(tx)
    log(f"{'✅' if ok else '❌'} Approve: {txh[:16]}...")

# ════════════════════════════════════════════
# SWAP FUNCTIONS
# ════════════════════════════════════════════

def swap_ankr_ke_usdt(ankr_amount):
    """ANKR native → USDT via V3 multicall"""
    amount_in = w3.to_wei(ankr_amount, "ether")
    deadline  = int(time.time()) + 300

    # exactInputSingle: WANKR → USDT, recipient langsung wallet
    swap_data = router.encode_abi(
        "exactInputSingle",
        args=[(
            Web3.to_checksum_address(WANKR_TOKEN),
            Web3.to_checksum_address(USDT_TOKEN),
            FEE_TIER,
            WALLET,      # terima USDT langsung
            amount_in,
            0,           # amountOutMinimum = 0 (testnet, ok)
            0            # sqrtPriceLimitX96
        )]
    )

    tx = router.functions.multicall(
        deadline,
        [swap_data]
    ).build_transaction({
        "chainId":  CHAIN_ID,
        "value":    amount_in,  # kirim ANKR native
        "gas":      300000,
        "gasPrice": w3.eth.gas_price,
        "from":     WALLET,
    })

    ok, txh = kirim_tx(tx)
    log(f"{'✅' if ok else '❌'} ANKR→USDT | {ankr_amount} ANKR | tx: {txh[:16]}...")
    return ok

def swap_usdt_ke_ankr():
    """USDT → ANKR native via V3 multicall + unwrapWETH9"""
    usdt_balance = usdt.functions.balanceOf(WALLET).call()
    if usdt_balance == 0:
        log("⚠️  Tidak ada USDT")
        return False

    approve_token(usdt, ZOTTO_ROUTER, usdt_balance)

    deadline = int(time.time()) + 300

    # Step 1: USDT → WANKR, recipient = router (untuk di-unwrap)
    swap_data = router.encode_abi(
        "exactInputSingle",
        args=[(
            Web3.to_checksum_address(USDT_TOKEN),
            Web3.to_checksum_address(WANKR_TOKEN),
            FEE_TIER,
            Web3.to_checksum_address(ZOTTO_ROUTER),  # router dulu
            usdt_balance,
            0,
            0
        )]
    )

    # Step 2: unwrap WANKR → ANKR native ke wallet
    unwrap_data = router.encode_abi(
        "unwrapWETH9",
        args=[0, WALLET]
    )

    tx = router.functions.multicall(
        deadline,
        [swap_data, unwrap_data]
    ).build_transaction({
        "chainId":  CHAIN_ID,
        "value":    0,
        "gas":      350000,
        "gasPrice": w3.eth.gas_price,
        "from":     WALLET,
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

## 📊 Volume Progress

| Target | Terkumpul | Progress |
|--------|-----------|----------|
| ${target:,.0f} | ${volume_terkumpul:,.4f} | {persen:.1f}% |

## 📈 Statistik
- Total swap: **{swap_ke}x**
""")

# ════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════

def main():
    print("╔══════════════════════════════════════╗")
    print("║     ZOTTO AUTO SWAP BOT — Mulai!     ║")
    print("╚══════════════════════════════════════╝\n")

    if not w3.is_connected():
        log("❌ Gagal konek ke Neura Testnet.")
        return

    log(f"✅ Terhubung ke Neura Testnet (Chain ID: {CHAIN_ID})")
    log(f"👛 Wallet: {WALLET}\n")

    ankr_bal, _ = cek_balance()
    if ankr_bal < SWAP_AMOUNT_ANKR + 1:
        log(f"❌ Balance tidak cukup. Perlu minimal {SWAP_AMOUNT_ANKR + 1} ANKR")
        return

    volume_per_putaran = SWAP_AMOUNT_ANKR * HARGA_ANKR_USD * 2
    putaran_dibutuhkan = int(TARGET_VOLUME_USD / volume_per_putaran) + 1
    log(f"📋 Target: ${TARGET_VOLUME_USD:,} | Estimasi putaran: {putaran_dibutuhkan}x\n")

    volume_terkumpul = 0.0
    swap_ke = 0

    for i in range(putaran_dibutuhkan):
        log(f"── Putaran {i+1}/{putaran_dibutuhkan} ──")

        if swap_ankr_ke_usdt(SWAP_AMOUNT_ANKR):
            volume_terkumpul += SWAP_AMOUNT_ANKR * HARGA_ANKR_USD
            swap_ke += 1
            time.sleep(DELAY_ANTAR_SWAP)

            if swap_usdt_ke_ankr():
                volume_terkumpul += SWAP_AMOUNT_ANKR * HARGA_ANKR_USD
                swap_ke += 1

        log(f"📊 Volume: ${volume_terkumpul:.4f} / ${TARGET_VOLUME_USD:,}")
        update_progress(swap_ke, volume_terkumpul, TARGET_VOLUME_USD)

        if volume_terkumpul >= TARGET_VOLUME_USD:
            log(f"\n🎉 TARGET TERCAPAI! Total swap: {swap_ke}x")
            break

        ankr_bal, _ = cek_balance()
        if ankr_bal < SWAP_AMOUNT_ANKR + 0.5:
            log("⚠️  Balance ANKR hampir habis, bot berhenti.")
            break

        time.sleep(DELAY_ANTAR_SWAP)

    log("\n✅ Bot selesai!")

if __name__ == "__main__":
    main()
