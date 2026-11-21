from pathlib import Path
import importlib.util
_contract_path=Path(__file__).resolve().parents[2]/'contracts'/'worker_registry.py'
_contract_spec=importlib.util.spec_from_file_location('ngs_worker_registry_contract',_contract_path)
_contract=importlib.util.module_from_spec(_contract_spec)
_contract_spec.loader.exec_module(_contract)
validate_registry=_contract.validate_registry
validate_active_registry=_contract.validate_active_registry
