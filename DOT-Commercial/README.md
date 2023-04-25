DOT Commercial https://ai.fmcsa.dot.gov/SMS/Tools/Downloads.aspx

## Processing Steps

This data set is loaded and configured in 5 steps.
1. `crashes-ingestion-setup` - create a pipeline that creates a fingerprint from two fields to be sued as the `id` field
1. `crashes` - create an index and load the crash data
1. `inspections` - create an index and load the vehicle inpsections data
1. `carriers-ingestion-setup` - create the enrichment indexes on `crashes` and `inspections` and an ingestion pipeline that uses them
1. `carriers` - create an index and load the carriers data using the pipeline to enrich `carriers` with data from `crashes` and `inspections`

We could have combined some of the setup and indexing steps and used the phase boundaries but this seemed to be an easier partitioning scheme to use just needing the `--step` parameter for partial work

## Processing Phases
Each step can contain one or more phases as described by json configuration files. Phases represent the type of work that can be done in one or more steps.  Each step can contain zero or more phases.
See [README.md](../README.md)

## Index Data
The data is organized and related as follows.

```mermaid
flowchart LR
    subgraph crashes-graph[crashes]
        crashes-alias[alias] -..-|points at| crashes[crashes index]
        crashes --> | optimized index| crashes-enrichment[crashes enrichment index]
    end
    subgraph inspections-graph[inspections]
        inpsections-alias[alias] -..-|points at| inspections[inspections index]
        inspections --> |optimized index| inspections-enrichment[inspections enrichment index]
    end
    subgraph carriers-graph[carriers]
        crashes-enrichment -.->|enriches| carriers-core
        inspections-enrichment -.->|enriches| carriers-core
        carriers-alias[alias] -..- | points at| carriers-core[carriers index]
    end
```

## Flow
An integrated view of the steps and phases.

```mermaid
flowchart LR
    subgraph steps
        direction LR
        crashes-ingestion-setup-step[crashes ingestion setup]
        crashes-step[crashes]
        inspections-step[inpsections]
        carriers-step[carriers]
        carriers-ingestion-setup-step[carriers ingestion setup]
    end

    subgraph indexes
        direction LR
        crashes-index["crashes-{day}-000001"] -..- crashes-alias[alias]
        inspections-index["inspections-{day}-000001"] -..- inspections-alias[alias]
        carriers-index["carriers-{day}-000001"] -..-> carriers-alias[alias]

        crashes-enrichment-index[crashes enrichment]
        inspections-enrichment-index[inspections enrichment]
    end

    subgraph datasets
        direction LR
        crashes-csv[crashes csv]
        inspections-csv[inspections csv]
        carriers-csv[carriers csv]
    end

    subgraph crashes-pipelines[ crashes pipelines]
        direction LR
        crashes-pipeline
    end

    subgraph carriers-pipelines[ carriers pipelines]
        direction LR
        enriching-pipeline
    end

    crashes-step -->|index-populate| crashes-pipeline
    crashes-step -->|index-map| crashes-index
    inspections-step -->|index-map| inspections-index
    inspections-step -->|index-populate | inspections-index
    carriers-step --> | index-map | carriers-index
    carriers-step --> | index-populate| enriching-pipeline

    crashes-csv-->|import| crashes-step
    inspections-csv -->|import| inspections-step
    carriers-csv -->|import| carriers-step

    crashes-pipeline -->|populate| crashes-index

    crashes-enrichment-index -.->|enrich-policies| enriching-pipeline
    inspections-enrichment-index -.->|enrich-policies| enriching-pipeline
    enriching-pipeline -->|populate| carriers-index

    crashes-ingestion-setup-step -.->|"pipelines (create)"| crashes-pipeline

    carriers-ingestion-setup-step -.->|enrichment-policies| crashes-enrichment-index
    carriers-ingestion-setup-step -.->|enrichment-policies| inspections-enrichment-index
    carriers-ingestion-setup-step -.->|"pipelines (create)"| enriching-pipeline


```