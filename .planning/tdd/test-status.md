# Test Status — owned by tdd-tester (feat/llama-cpp-runtime-n-ctx-seq)
_Append-only per task._

| task-id | status | notes |
|---------|--------|-------|
| T1 | red | 4 new tests in TestRuntimeNCtxSeqIntrospection: runtime-lookup (FAIL — current code uses configured 512, not 252), fallback (PASS — current code IS the fallback shape), floor (FAIL), once-per-instance log (FAIL — no such log yet). RED confirmed; ready for coder. |
