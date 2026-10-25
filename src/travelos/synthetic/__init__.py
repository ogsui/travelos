"""Deterministic, obviously fake synthetic data for tests and evals (task 007)."""

from travelos.synthetic.datasets import DEFAULT_SEED, GENERATOR_VERSION
from travelos.synthetic.generator import SyntheticDataset, generate_dataset
from travelos.synthetic.loader import SeedCrmReport, seed_crm, write_dataset

__all__ = [
    "DEFAULT_SEED",
    "GENERATOR_VERSION",
    "SeedCrmReport",
    "SyntheticDataset",
    "generate_dataset",
    "seed_crm",
    "write_dataset",
]
