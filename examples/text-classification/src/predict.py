import argparse
import json

import joblib

parser = argparse.ArgumentParser()
parser.add_argument("model")
parser.add_argument("text", nargs="+")
args = parser.parse_args()
model = joblib.load(args.model)
print(json.dumps(model.predict(args.text).tolist()))
