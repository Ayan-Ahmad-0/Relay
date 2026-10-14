# Relay Benchmark Report - 2026-10-14

**Mode:** `SMALL`  
**Duration:** `21.98s`  
**Execution Timestamp:** `2026-10-14T07:08:45.946351+00:00`

---

## 📊 Summary Scorecard

| Invariant / Benchmark Area | Metric Tested | Result | Standard / SLA | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Crash Fault Recovery** | Duplicate Applied Effects | **0** | Exactly 0 across 5 crash windows | ✅ PASS |
| **Deterministic Replay** | Replay Divergences | **0** | 0 divergence on event replay | ✅ PASS |
| **Prompt Injection Defense** | Unapproved Applied Effects | **0** | Exactly 0 unapproved effects | ✅ PASS |
| **Concurrency Contention** | Terminal Resolution Rate | **100.0%** | 100% terminal resolution | ✅ PASS |
| **Concurrency Serialization** | Permanent Serialization Errors | **0** | 0 unhandled deadlocks/skews | ✅ PASS |
| **Cost Metering & Timeout** | Billed-Unknown Reconciliation | **10¢ delta** | Exact delta bounds matching timeout events | ✅ PASS |
| **Mutation Testing** | Mutant Kill Rate | **100.0%** | >= 80.0% mutant kill rate | ✅ PASS |

---

## 🔬 Benchmark Details

### 1. Chaos Fault Injection
- **Total Injected Crashes:** 10
- **External Sink Unique Requests:** 10
- **Duplicate Applied Effects Detected:** **0**
- **Successful Recoveries:** 10/10

### 2. Adversarial Injection Split
- **Total Corpus:** 17 adversarial test vectors
- **Model Compliances:** 17
- **Policy Gate Interceptions:** 17
- **Unapproved Applied Effects:** **0**

### 3. Contention & Quota Scaling
- **Tested Worker Counts:** [2, 4]
- **Peak Throughput:** 52.4 runs/sec with 2 workers
- **Terminal State Resolution:** 100%

### 4. Survivable Cost Ledger
- **Completed Calls:** 8
- **Timeouts Handled (billed-unknown):** 1
- **Budget Ceiling Blocks:** 1
- **Reconciliation Valid:** True

### 5. Mutation Testing
- **Target Module:** `relay/security.py`
- **Mutants Tested:** 7
- **Mutants Killed:** 7 (100.0%)
