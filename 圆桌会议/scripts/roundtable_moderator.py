"""Deterministic discussion routing; does not judge semantic agreement or disagreement."""


def choose(bundle, requested=None):
    session, turns = bundle["session"], bundle["turns"]
    ids = [p["person_id"] for p in session["participants"]]
    if requested is not None and requested not in ids:
        raise ValueError("Unknown requested speaker")
    counts = {pid: sum(t["speaker_id"] == pid for t in turns) for pid in ids}
    last = {pid: max((t["sequence"] for t in turns if t["speaker_id"] == pid), default=0) for pid in ids}
    claims = {c["claim_id"]: (t, c) for t in turns for c in t["claims"]}
    addressed = {target for t in turns for target in t["responds_to"]}
    addressed.update(c["responds_to_claim_id"] for t in turns for c in t["claims"] if c.get("responds_to_claim_id"))
    opening = next((pid for pid in ids if counts[pid] == 0), None)
    speaker = requested or opening
    reason = "explicit_user_selection" if requested else "opening_opportunity"
    if speaker is None:
        # When a claim was directly addressed, offer its author a chance to answer.
        latest = turns[-1]
        targets = latest["responds_to"] + [c["responds_to_claim_id"] for c in latest["claims"] if c.get("responds_to_claim_id")]
        owners = [claims[target][0]["speaker_id"] for target in targets if target in claims]
        speaker = next((owner for owner in owners if owner != latest["speaker_id"] and counts[owner] <= min(counts.values()) + 1), None)
        reason = "reply_opportunity"
        if speaker is None:
            eligible = [pid for pid in ids if pid != latest["speaker_id"]] or ids
            speaker = min(eligible, key=lambda pid: (counts[pid], last[pid], ids.index(pid)))
            reason = "least_heard_available_perspective"
    pending = session["current"]["pending_user_event_id"]
    if pending:
        targets = [pending]
        instruction = "回应用户刚补充的问题或条件；可以修正自己的判断，不替其他人物作答。"
        reason += ";pending_user_interjection"
    elif counts[speaker] == 0:
        targets = ["opening"]
        instruction = "围绕当前问题给出自己的切入点；可回应已有发言，不必重复别人或刻意制造分歧。"
    else:
        candidates = [(t, c) for t, c in claims.values() if t["speaker_id"] != speaker and c["novelty"] != "withdrawn"]
        if candidates:
            turn, claim = min(candidates, key=lambda pair: (pair[1]["claim_id"] in addressed, -pair[0]["sequence"], pair[1]["claim_id"]))
            targets = [claim["claim_id"]]
        else:
            previous = next((t for t in reversed(turns) if t["speaker_id"] != speaker), turns[-1] if turns else None)
            targets = [previous["turn_id"]] if previous else ["opening"]
        instruction = "回应指定的具体论点；可以支持、追问、修正或保留分歧，不要求反驳或达成共识。"
    return {"schema_version": "1.0", "action": "speak", "next_speaker": speaker,
            "responds_to": targets, "instruction": instruction, "reason": reason, "stop_signal": "none"}
