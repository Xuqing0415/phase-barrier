---- MODULE PhaseBarrier ----
(***************************************************************************)
(* Formal specification of the phase-barrier stage machine (path 2).       *)
(*                                                                          *)
(* This is the "strict" configuration: all five defense lines are treated   *)
(* as enabled, so a missing/disabled line is NOT modelled as "passed".      *)
(*                                                                          *)
(* Stages: 0 requirement, 1 spec, 2 tests, 3 implementation, 4 test run,    *)
(*         5 repair, 6 delivery.                                            *)
(*                                                                          *)
(* Defense lines (anti_shortcut.defense.BUILTIN_DEFENSE_LINES):             *)
(*   defense[1] requirement template   (gate 1 -> 2)                        *)
(*   defense[2] dual review            (gate 1 -> 2)                        *)
(*   defense[3] formal check           (gate 1 -> 2)                        *)
(*   defense[4] behavior audit         (gate 4 -> 6, 5 -> 6)                *)
(*   defense[5] human review           (gate 4 -> 6, 5 -> 6)                *)
(*                                                                          *)
(* Allowed stage transitions (everything else must be rejected):            *)
(*   0->1  1->2  2->3  3->4  4->5  4->6  5->6                               *)
(* where 4->6 is the documented "green tests, skip the repair stage"        *)
(* shortcut implemented by skill.advance_stage.                             *)
(*                                                                          *)
(* Abstraction notes:                                                       *)
(* - code versions are counted instead of wall-clock timestamps, so the      *)
(*   state space stays FINITE (an unbounded Nat clock would make the model   *)
(*   infinite-state and TLC would never terminate);                          *)
(* - MaxSeq = 2 code versions are enough to exercise every guard:            *)
(*   write -> test -> deliver, and write -> test -> repair -> write ->       *)
(*   test -> deliver;                                                        *)
(* - post-delivery tool calls are not modelled (the implementation does not  *)
(*   hard-freeze the workspace), which is why "fresh test at delivery" is    *)
(*   captured by the dedicated variable freshAtDelivery.                     *)
(***************************************************************************)
EXTENDS Naturals, TLC

CONSTANTS MaxStage, MaxSeq

ASSUME MaxStage = 6
ASSUME MaxSeq = 2

VARIABLES
    stage,           (* current stage, 0..MaxStage                              *)
    codeSeq,         (* number of completed source changes (code versions)      *)
    testedSeq,       (* codeSeq value at the time of the last test run          *)
    testPassed,      (* did the last test run pass?                             *)
    defense,         (* defense[i] = TRUE  <=>  defense line i has passed       *)
    delivered,       (* has the task reached the delivery stage?                *)
    freshAtDelivery  (* at delivery time: testPassed /\ testedSeq = codeSeq     *)

vars == <<stage, codeSeq, testedSeq, testPassed, defense, delivered,
         freshAtDelivery>>

(* The last test ran against the current code version and passed.           *)
Fresh == testPassed /\ testedSeq = codeSeq

TypeOK ==
    /\ stage \in 0..MaxStage
    /\ codeSeq \in 0..MaxSeq
    /\ testedSeq \in 0..MaxSeq
    /\ testPassed \in BOOLEAN
    /\ defense \in [1..5 -> BOOLEAN]
    /\ delivered \in BOOLEAN
    /\ freshAtDelivery \in BOOLEAN

Init ==
    /\ stage = 0
    /\ codeSeq = 0
    /\ testedSeq = 0
    /\ testPassed = FALSE
    /\ defense = [i \in 1..5 |-> FALSE]
    /\ delivered = FALSE
    /\ freshAtDelivery = FALSE

(***************************************************************************)
(* Actions                                                                  *)
(***************************************************************************)

RecordRequirement ==
    /\ stage = 0
    /\ stage' = 1
    /\ UNCHANGED <<codeSeq, testedSeq, testPassed, defense, delivered,
                   freshAtDelivery>>

PassDefense(i) ==
    /\ i \in 1..5
    /\ defense' = [defense EXCEPT ![i] = TRUE]
    /\ UNCHANGED <<stage, codeSeq, testedSeq, testPassed, delivered,
                   freshAtDelivery>>

(* Source changes are only allowed in the implementation (3) and repair (5) *)
WriteCode ==
    /\ stage \in {3, 5}
    /\ codeSeq < MaxSeq
    /\ codeSeq' = codeSeq + 1
    /\ testPassed' = FALSE
    /\ UNCHANGED <<stage, testedSeq, defense, delivered, freshAtDelivery>>

(* A test can only run once code exists, and only from implementation on     *)
RunTest(passed) ==
    /\ stage >= 3
    /\ codeSeq > 0
    /\ testedSeq' = codeSeq
    /\ testPassed' = passed
    /\ UNCHANGED <<stage, codeSeq, defense, delivered, freshAtDelivery>>

(* Gate 1 -> 2: defense lines 1, 2 and 3 must have passed first             *)
AdvanceToTests ==
    /\ stage = 1
    /\ defense[1] /\ defense[2] /\ defense[3]
    /\ stage' = 2
    /\ UNCHANGED <<codeSeq, testedSeq, testPassed, defense, delivered,
                   freshAtDelivery>>

AdvanceToImplementation ==
    /\ stage = 2
    /\ stage' = 3
    /\ UNCHANGED <<codeSeq, testedSeq, testPassed, defense, delivered,
                   freshAtDelivery>>

AdvanceToTestRun ==
    /\ stage = 3
    /\ stage' = 4
    /\ UNCHANGED <<codeSeq, testedSeq, testPassed, defense, delivered,
                   freshAtDelivery>>

(* Gate 4 -> 6: fresh green test + defense lines 4 and 5                    *)
DeliverFromTestRun ==
    /\ stage = 4
    /\ Fresh
    /\ defense[4] /\ defense[5]
    /\ stage' = MaxStage
    /\ delivered' = TRUE
    /\ freshAtDelivery' = Fresh
    /\ UNCHANGED <<codeSeq, testedSeq, testPassed, defense>>

(* Gate 4 -> 5: failing test, or source changed after the last test run     *)
EnterRepair ==
    /\ stage = 4
    /\ ~Fresh
    /\ stage' = 5
    /\ UNCHANGED <<codeSeq, testedSeq, testPassed, defense, delivered,
                   freshAtDelivery>>

(* Gate 5 -> 6: regression test must be fresh and green + defenses 4, 5     *)
DeliverFromRepair ==
    /\ stage = 5
    /\ Fresh
    /\ defense[4] /\ defense[5]
    /\ stage' = MaxStage
    /\ delivered' = TRUE
    /\ freshAtDelivery' = Fresh
    /\ UNCHANGED <<codeSeq, testedSeq, testPassed, defense>>

Next ==
    \/ RecordRequirement
    \/ (\E i \in 1..5 : PassDefense(i))
    \/ WriteCode
    \/ (\E p \in BOOLEAN : RunTest(p))
    \/ AdvanceToTests
    \/ AdvanceToImplementation
    \/ AdvanceToTestRun
    \/ DeliverFromTestRun
    \/ EnterRepair
    \/ DeliverFromRepair

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Core invariants (INV-1 .. INV-8 in docs/formal-invariants.md)            *)
(***************************************************************************)

INV_TypeOK == TypeOK

(* INV-1: delivery implies a passing test that is newer than the code       *)
INV_DeliveryNeedsFreshTest == delivered => freshAtDelivery

(* INV-2: source changes can only happen from the implementation stage on   *)
INV_CodeOnlyAfterImplementation == codeSeq > 0 => stage >= 3

(* INV-3: delivery implies the behavior audit passed                        *)
INV_DeliveryNeedsBehaviorAudit == delivered => defense[4]

(* INV-4: defense lines 1/2/3 gate every stage from 2 onwards               *)
INV_StageGates == stage >= 2 => (defense[1] /\ defense[2] /\ defense[3])

(* INV-5: delivery implies the human review line passed                     *)
INV_DeliveryNeedsHumanReview == delivered => defense[5]

(* INV-6: the stage never leaves 0..MaxStage                                *)
INV_StageBound == stage \in 0..MaxStage

(* INV-7: delivery is terminal (the stage stays at MaxStage)                *)
INV_DeliveredIsFinal == delivered => stage = MaxStage

(* INV-8: tests can only target a code version that already exists          *)
INV_TestedVersionBound == testedSeq <= codeSeq

(* INV-9: a reported passing test implies a test actually ran on code       *)
INV_TestPassedImpliesRun == testPassed => (codeSeq > 0 /\ testedSeq > 0)

====
