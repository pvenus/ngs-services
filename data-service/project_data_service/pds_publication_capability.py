"""Read-only metadata for the installed, pre-INSERT serializer contract."""
import hashlib
from pathlib import Path
from . import concept_commit_preview

SERIALIZER_SHA256 = '135b888fc1f30d51571c5890621409bb37e5a39e08ebad4be3717eebee95d596'


def publication_capability():
    # Refuse a partial/mismatched deployment; do not report an expected hash for
    # a different serializer file. No DB construction or state mutation.
    actual = hashlib.sha256(Path(concept_commit_preview.__file__).read_bytes()).hexdigest()
    if actual != SERIALIZER_SHA256:
        raise ValueError('PDS_PUBLICATION_SERIALIZER_MISMATCH')
    return {'schemaVersion': 'pds-concept-publication-capability.v1',
            'envelopeBudgetContract': 'pds-concept-envelope-budget.v1',
            'serializerSha256': actual, 'validatesBeforeInsert': True,
            'serverGeneratedSavedAt': True, 'supportsReadOnlyPreview': True}


def require_publication_capability(value):
    if value != publication_capability():
        raise ValueError('PDS_PUBLICATION_CAPABILITY_REQUIRED')
