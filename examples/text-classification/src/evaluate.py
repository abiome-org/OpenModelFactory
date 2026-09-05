import argparse
import json
from pathlib import Path

import joblib
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = json.loads(args.data.read_text())
    model = joblib.load(args.model)
    predictions = model.predict([row["text"] for row in rows])
    expected = [row["label"] for row in rows]
    metrics = {
        "accuracy": accuracy_score(expected, predictions),
        "spam_f1": f1_score(expected, predictions, pos_label="spam", zero_division=0),
        "spam_precision": precision_score(expected, predictions, pos_label="spam", zero_division=0),
        "spam_recall": recall_score(expected, predictions, pos_label="spam", zero_division=0),
        "passed": len(predictions) == len(rows) and len(rows) > 0,
        "compatibilityPassed": set(predictions).issubset({"ham", "spam"}),
    }
    examples = [
        {
            "id": row["id"],
            "input": row["text"],
            "expected": row["label"],
            "prediction": str(prediction),
            "score": int(prediction == row["label"]),
        }
        for row, prediction in zip(rows, predictions, strict=True)
    ]
    (args.output / "metrics.json").write_text(json.dumps(metrics))
    (args.output / "examples.json").write_text(json.dumps(examples))


if __name__ == "__main__":
    main()
