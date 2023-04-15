DOT Commercial https://ai.fmcsa.dot.gov/SMS/Tools/Downloads.aspx

## Index Data

```mermaid
flowchart LR
    subgraph crashes-graph[crashes]
        crashes[crashes index]
        crashes --> | optimized index| crashes-enrichment[crashes enrichment index]
    end
    subgraph inspections-graph[inspections]
        inspections[inspections index]
        inspections --> |optimized index| inspections-enrichment[inspections enrichment index]
    end
    subgraph carriers-graph[carriers]
        carriers-core[carriers index]
        crashes-enrichment -.->|enriches| carriers-core
        inspections-enrichment -.->|enriches| carriers-core
    end
```
## Phases
Phases represent the type of work that can be done in one or more steps

```mermaid
flowchart LR
    subgraph phases
        index-map
        enrichment-policies
        pipelines
        index-populate
    end
```

## Flow

```mermaid
flowchart LR
    subgraph steps
        direction LR
        crashes-step[crashes]
        inspections-step[inpsections]
        carriers-step[carriers]
        carriers-ingestion-step[carriers ingestion]
    end

    subgraph indexes
        direction LR
        crashes-index[crashes]
        inspections-index[inspections]
        carriers-index[carriers]

        crashes-enrichment-index[crashes enrichment]
        inspections-enrichment-index[inspections enrichment]
    end

    subgraph datasets
        direction LR
        crashes-csv[crashes csv]
        inspections-csv[inspections csv]
        carriers-csv[carriers csv]
    end

    subgraph pipelines
        direction LR
        enriching-pipeline
    end

    crashes-step -->|index-map| crashes-index
    crashes-step -->|index-populate| crashes-index
    inspections-step -->|index-map| inspections-index
    inspections-step -->|index-populate | inspections-index
    carriers-step --> | index-map | enriching-pipeline
    carriers-step --> | index-populate| enriching-pipeline

    crashes-csv-->|import| crashes-step
    inspections-csv -->|import| inspections-step
    carriers-csv -->|import| carriers-step

    crashes-enrichment-index -.->|enrich-policies| enriching-pipeline
    inspections-enrichment-index -.->|enrich-policies| enriching-pipeline
    enriching-pipeline -->|populate| carriers-index


    carriers-ingestion-step -.->|enrichment-policies| crashes-enrichment-index
    carriers-ingestion-step -.->|enrichment-policies| inspections-enrichment-index
    carriers-ingestion-step -.->|pipelines| enriching-pipeline


```