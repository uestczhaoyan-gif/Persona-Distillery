"""Owner's local terminal workflow for reviewing candidates and building drafts."""
import argparse
import json
from pathlib import Path
import sys

import local_distill as local
import candidate_review
import spec_review
import local_merge
import local_draft


def decision_file(args):
    # Policy and storage approval precede reading the external decision body.
    _, paths, _ = local.load_job(args.library, args.job, confirm_local_storage=args.confirm_local_storage)
    path = local.check_private_location(args.decision)
    if any(path.is_relative_to(Path(p).resolve()) for p in (args.library, paths["incoming"], paths["personas"])):
        raise ValueError("Keep decision inputs outside sources and generated directories")
    return local_merge.bounded_json(path)[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--confirm-local-storage", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, field in (("inspect-candidate", "run"), ("review-candidate", "run"),
                        ("inspect-spec", "spec"), ("review-spec", "spec"),
                        ("build-draft", "review"), ("plan-resume", "review"), ("resume-draft", "review")):
        command = commands.add_parser(name)
        command.add_argument("--" + field, required=True)
        if name in {"review-candidate", "review-spec"}:
            command.add_argument("--decision", type=Path, required=True)
        if name == "resume-draft":
            command.add_argument("--plan", required=True)
    merge = commands.add_parser("merge")
    merge.add_argument("--review", action="append", required=True, help="Ordered review hashes; repeat for each history item")
    merge.add_argument("--previous")
    merge.add_argument("--decision", type=Path, help='Optional object containing only "resolutions" array')
    args = parser.parse_args(argv)
    try:
        if not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
            raise ValueError("Requires the owner's local terminal")
        if not args.confirm_local_storage:
            raise ValueError("Local storage confirmation required")
        common = {"confirm_local_storage": True}
        if args.command == "inspect-candidate":
            result = candidate_review.read_run(args.library, args.job, args.run, **common)
        elif args.command == "review-candidate":
            result = candidate_review.save_review(args.library, args.job, args.run, decision_file(args), **common)
        elif args.command == "inspect-spec":
            result = spec_review.read_spec(args.library, args.job, args.spec, **common)
        elif args.command == "review-spec":
            result = spec_review.save_spec_review(args.library, args.job, args.spec, decision_file(args), **common)
        elif args.command == "merge":
            resolutions = None
            if args.decision:
                value = decision_file(args)
                if set(value) != {"resolutions"} or not isinstance(value["resolutions"], list):
                    raise ValueError("Expected resolutions array")
                resolutions = value["resolutions"]
            result = local_merge.save_merge(args.library, args.job, args.review,
                previous_merge=args.previous, resolutions=resolutions, **common)
        elif args.command == "build-draft":
            result = local_draft.build_draft(args.library, args.job, args.review, **common)
        elif args.command == "plan-resume":
            result = local_draft.plan_resume(args.library, args.job, args.review, **common)
        else:
            result = local_draft.resume_draft(args.library, args.job, args.review, args.plan, **common)
        # JSON escapes terminal control bytes while keeping Chinese review text readable.
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RecursionError):
        print(json.dumps({"ok": False, "error": "Local review failed; inspect local inputs and workflow documentation"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
