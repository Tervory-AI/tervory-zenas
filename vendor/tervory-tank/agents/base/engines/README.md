# Base vertical packages

`ffl/manifest.json` defines the existing FFL package: reference/gold entrypoints,
versioned Skala skills and SHA-256 inventory. Skill files remain at their existing
Skala paths so approved references and runtime behavior remain unchanged. This is
a logical package; the manifest does not activate or approve a skill.

Create a new vertical offline:

    python -m agents.base.engines.scaffold your_vertical /path/to/engines

The template creates an empty, unregistered package. Implement the vertical's
reference mapping and data in that package, add versioned skills with evaluation
cases, and declare their paths and hashes in its manifest. Core Base stays
industry-neutral. Activation and Skala approval use the existing review process.
SOT and compliance flags remain empty; no synthetic business records are generated.
