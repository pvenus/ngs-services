"""Versioned worker configuration. Never reads user knowledge or databases."""
import copy
import json
from pathlib import Path

def entries():
    return json.loads(Path(__file__).with_name('execution_contracts.json').read_text())['contracts']

def contract_id(value):
    # Read-only compatibility for retained catalog metadata. No data migration.
    return 'template.processing-spec.contract' if value == 'template.processing-spec.knowledge' else value

def get(value):
    matches = [entry for entry in entries() if entry['id'] == contract_id(value)]
    if len(matches) != 1:
        return None
    return copy.deepcopy(matches[0])
