"""Registry only: existing law storage and its ingestion owner remain unchanged."""


def reference_corpora(agent: str, region: str) -> list[dict]:
    if agent != "zenas" or region not in ("za", "us-east"):
        return []
    return [
        dict(
            id="zenas-law",
            agent="zenas",
            tier="reference",
            read_only=True,
            region=region,
            storage="zenas_pool",
            search="/law/search",
            owner="existing-zenas-pool-loader",
        )
    ]
