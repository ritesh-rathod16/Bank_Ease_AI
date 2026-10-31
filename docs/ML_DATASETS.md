# Dataset provenance and acquisition

## Current local data

The checked-in scripts generate fictional documents. Runtime copies, labels, checkpoints, and prepared data live under ignored `data/runtime/ml/`. The generated labels have not been human reviewed. The existing GPT-2 sequence-classification experiment is synthetic-only and is not a conversation or financial-decision model.

## Reviewed sources

| Source | License / rights notes | Intended use | Status |
| --- | --- | --- | --- |
| [FinQA GitHub](https://github.com/czyssrs/FinQA) and [IBM Research dataset card](https://huggingface.co/datasets/ibm-research/finqa) | Card labels dataset CC BY 4.0; GitHub code is MIT. The annual-report source text has its own provenance and rights. Attribute and review source terms. | Numerical financial reasoning, financial QA | Acquired at commit `0f16e2867befa6840783e58be38c9efb9229d742` (8,281 examples); prepared family-disjoint splits. Research only; not approved for model training until source rights are reviewed. |
| [CUAD](https://huggingface.co/datasets/theatticusproject/cuad-qa) | Dataset annotations CC BY 4.0; the authors disclaim blanket rights/warranty over underlying contracts. | Clause extraction and contract QA research | Deferred pending contract-text rights review; not downloaded. |
| [DocLayNet](https://huggingface.co/datasets/docling-project/DocLayNet) | CDLA-Permissive-1.0; inspect exact version and included source metadata. | Layout and page-region analysis | Deferred; larger image/layout data is not needed for current text assistant. |
| [FUNSD primary project](https://guillaumejaume.github.io/FUNSD/) | Primary-source terms were not verified in this audit. Third-party mirrors declare varying licenses. | Noisy form layout and OCR | Do not download until upstream terms are confirmed. |
| [RBI official website](https://www.rbi.org.in/) | Review terms per publication; source pages have dates and versions. | Date-tracked retrieval and citations | Retrieval source only; no bulk regulatory training download. |

The machine-readable catalog is `config/public_dataset_catalog.json`. It distinguishes permitted-use questions from retrieval utility and does not put source datasets into the customer RAG index.

## Resumable FinQA acquisition

The acquisition script uses GitHub's public API and raw files, pins the upstream commit, limits acquisition to `train.json`, `dev.json`, and `test.json`, excludes private test data, checks available disk space and maximum size, resumes partial downloads, hashes files, and saves provenance:

```powershell
python scripts/acquire_public_dataset.py --accept-license-review --max-bytes 125829120
python scripts/prepare_finqa.py
```

The three upstream files total 103,566,417 bytes, so the acquisition command raises the default 32 MiB cap to 120 MiB. `--accept-license-review` is required; the dataset card declares CC BY 4.0, while underlying report rights require separate review. The reviewed download is under ignored `data/runtime/ml/public/finqa-v1/` and has a manifest with upstream commit, file hashes, attribution, and limitations.

Preparation validates hashes, deduplicates exact IDs, groups by company/year report family, then deterministically reassigns whole families into 80/10/10 train/dev/test partitions while preserving the original split in `upstream_split`. The upstream assignment had 355 of 799 families across multiple splits; the prepared split has 6,642/791/848 examples and 639/80/80 disjoint families. Its manifest records this leakage and sets `training_approved` to `false` until rights and task suitability are reviewed. Data stays separate from user documents and conversations; it is not indexed as current financial guidance.
