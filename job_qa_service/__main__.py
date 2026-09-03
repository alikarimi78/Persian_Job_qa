from . import JobQAEngine
from .config import OCCUPATIONS_PATH

if __name__ == "__main__":
    engine = JobQAEngine(OCCUPATIONS_PATH)
    print("✅ Ready. Ask your question (or 'خروج').")
    while True:
        try:
            question = input("\n❓ سوال: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in ["exit", "quit", "خروج"]:
            break
        if not question:
            continue
        try:
            res = engine.answer(question)
        except Exception:
            continue
        print(f"\nmode: {res['mode']} | intent: {res['intent']}")
        if res["mode"] in ("single", "job_match", "job_generated"):
            print(f"job: {res['job']} (score={res['score']:.3f})")
        elif res["mode"] == "interdisciplinary":
            print(f"jobs: {res['jobs'][0]} + {res['jobs'][1]}")
        if res.get("related_jobs"):
            print(f"related: {'، '.join(res['related_jobs'])}")
        for detail in res.get("details", []):
            opened = "، ".join(f["label"] for f in detail["fields"] if f["primary"])
            folded = "، ".join(f["label"] for f in detail["fields"] if not f["primary"])
            print(f"details [{detail['job_title']}] open: {opened} | folded: {folded}")
        print(f"\n🤖 {res['answer']}")
