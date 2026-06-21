# AI-Powered Transaction Processing Pipeline

This repository contains a production-grade, asynchronous financial transaction processing backend. It is designed to ingest raw (dirty) CSV transaction data, perform strict data cleaning and normalization, flag statistical and business-rule anomalies, batch-classify missing information using Google's Gemini 1.5 Flash LLM, and generate a narrative spending summary with risk assessment.

---

## 1. Project Overview

Financial exports are notoriously dirty, containing inconsistent casing, malformed dates, mismatched currency notation, missing categories, and anomalies. This pipeline solves these challenges by providing:
*   **Immediate API Feedback**: Accepts large CSV uploads, validates structure, stores files locally, enqueues work, and returns execution tokens immediately.
*   **Idempotency & Fault Tolerance**: Handled through atomic database transactions and pre-run cleaning sweeps.
*   **Intelligent Enrichment**: Batch-processes missing categories using the Gemini 1.5 Flash API with exponential backoff retries.
*   **Advanced Analytics**: Auto-detects statistical outliers (amount > 3x account median) and currency/merchant mismatches (e.g. USD used for Swiggy, Ola, or IRCTC).

---

## 2. Architecture Overview

The system divides responsibilities between a high-performance Web Server and a background Worker Pool using a **shared storage volume** to prevent broker serialization bottlenecks:

```
                  ┌─────────────────┐
                  │  Client / cURL  │
                  └────────┬────────┘
                           │ 
             HTTP REST APIs│ (Uploads, Polling)
                           ▼
                 ┌──────────────────┐
                 │  FastAPI (Web)   │
                 └────┬─────────┬───┘
                      │         │
    Saves CSV to disk │         │ Enqueues Job UUID
                      ▼         ▼
             ┌──────────────┐  ┌─────────────┐
             │uploads/*.csv │  │Redis Broker │
             └──────────────┘  └──────┬──────┘
                      ▲               │
     Reads raw file   │               │ Picks up task
                      │               ▼
                 ┌────┴─────────────────────┐
                 │  Celery Worker (Task)    │
                 └──────────────┬───────────┘
                                │
                 ┌──────────────┼──────────────┐
                 ▼              ▼              ▼
           ┌──────────┐   ┌───────────┐  ┌───────────┐
           │  Pandas  │   │  Gemini   │  │PostgreSQL │
           │ (Engine) │   │ (1.5 F)   │  │(Database) │
           └──────────┘   └───────────┘  └───────────┘
```

### Lifecycle of an Upload Request
1.  **Ingestion**: Client uploads a CSV to `POST /jobs/upload`. FastAPI validates file extension, parses headers, calculates raw row count, and registers a `pending` Job record.
2.  **Storage**: The file is stored in `uploads/{job_id}.csv` on a shared volume.
3.  **Dispatch**: FastAPI enqueues the task by sending only the `job_id` string to **Redis**. The web server returns `HTTP 202 Accepted` immediately.
4.  **Processing**: The **Celery worker** picks up the task, flags the database record as `PROCESSING`, and loads the CSV directly from the shared volume.
5.  **Execution**: The worker runs the data cleaning, anomaly checks, LLM category classifications, and summary generation.
6.  **Persistence**: Cleansed data, flagged anomalies, and summaries are persisted in a single atomic database transaction. The job is marked as `COMPLETED`.

---

## 3. Tech Stack

*   **API Framework**: FastAPI (Python 3.11)
*   **Database**: PostgreSQL 15 (SQLAlchemy 2.0 ORM)
*   **Task Queue**: Celery 5.3 with Redis 7.0 as Broker and Backend
*   **Data Processing**: Pandas 2.2 for vectorized cleaning and outlier analysis
*   **AI Engine**: Google Gemini 1.5 Flash API (`google-generativeai` SDK)
*   **Containerization**: Docker & Docker Compose

---

## 4. Folder Structure

```
Backend_DevOps_Assignment/
├── app/
│   ├── __init__.py
│   ├── config.py              # Configuration loading using Pydantic Settings
│   ├── database.py            # PostgreSQL connection pool and SessionLocal
│   ├── models.py              # SQLAlchemy 2.0 ORM Models (Job, Transaction, JobSummary)
│   ├── schemas.py             # Pydantic v2 schemas for request/response serialization
│   ├── worker.py              # Celery worker application and pipeline task orchestration
│   ├── routes/
│   │   ├── __init__.py
│   │   └── jobs.py            # REST API endpoints (Upload, status, results, list)
│   └── pipeline/
│       ├── __init__.py
│       ├── cleaning.py        # Pandas-based cleaning & normalization
│       ├── anomalies.py       # Vectorized anomaly rules & statistical medians
│       └── llm_service.py     # Gemini client with batching and exponential backoff
├── uploads/                   # Shared local directory storing raw CSV files
├── Dockerfile                 # Multi-stage, slim Python build recipe
├── docker-compose.yml         # Container orchestration (web, worker, postgres, redis)
├── requirements.txt           # Python application dependencies
├── .env.example               # Config template for deployment secrets
└── README.md                  # This documentation file
```

---

## 5. Setup & Configuration

### Environment Variables
To configure the application, create a `.env` file in the root directory based on the template below:

```ini
# Postgres settings
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=transactions_db
# In docker-compose, postgres host is resolved automatically
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/transactions_db

# Redis settings
REDIS_URL=redis://redis:6379/0

# Gemini API Key (Required for LLM processing)
GEMINI_API_KEY=your_actual_gemini_api_key_here

# FastAPI settings
ALLOWED_ORIGINS=*
UPLOAD_DIR=uploads
```

---

## 6. Execution Options

### Running with Docker (Recommended)
Make sure you have Docker and Docker Compose installed.

1.  **Configure Environment**: Copy `.env.example` to `.env` and fill in your `GEMINI_API_KEY`:
    ```bash
    cp .env.example .env
    ```
2.  **Start Services**: Build and start the stack in detached mode:
    ```bash
    docker compose up --build -d
    ```
    This spins up:
    *   `postgres` on port `5432` with healthcheck
    *   `redis` on port `6379` with healthcheck
    *   `web` (FastAPI) on port `8000`
    *   `worker` (Celery)
3.  **Tear Down Services**:
    ```bash
    docker compose down -v
    ```

### Running Locally
To run the components locally for testing, ensure PostgreSQL and Redis are running on your host machine.

1.  **Create Virtual Environment**:
    ```bash
    python -m venv venv
    venv\Scripts\activate      # Windows
    source venv/bin/activate    # Linux/macOS
    ```
2.  **Install Packages**:
    ```bash
    pip install -r requirements.txt
    ```
3.  **Run FastAPI Web Server**:
    ```bash
    uvicorn app.main:app --reload --port 8000
    ```
4.  **Run Celery Worker**:
    ```bash
    celery -A app.worker.celery worker --loglevel=info
    ```

---

## 7. The Processing Pipeline

When a Celery task starts, the DataFrame executes through the following distinct stages:

### A. Data Cleaning (`app/pipeline/cleaning.py`)
1.  **Null-Safe Enforcements**: Standardizes empty spaces, strings representing nulls (`"nan"`, `"None"`, `"null"`), and actual empty values into consistent, typed defaults.
2.  **Date Normalization**: Safely tries multiple date formats (e.g. `DD-MM-YYYY`, `YYYY/MM/DD`). Converts valid inputs into `datetime.date` objects.
3.  **Amount Normalization**: Cleans currency signs, whitespace, and commas using regex `re.sub(r'[^0-9.-]', '', value)` before converting to float.
4.  **Casing Normalization**: Forces `currency` and `status` to uppercase (defaulting to `"UNKNOWN"` if missing).
5.  **Row De-duplication**: Filters out exact row duplicates.
6.  **Metadata Accumulation**: Outputs a statistical dict tracing `original_rows`, `cleaned_rows`, `removed_duplicates`, and `invalid_rows` (rows dropped due to missing date/amount).

### B. Anomaly Detection (`app/pipeline/anomalies.py`)
*   **Statistical Outliers**: Groups transactions by `account_id` and computes the median amount. Transactions where `amount > 3 * account_median` are flagged.
*   **Currency Mismatch**: Flags transactions where `currency == "USD"` for domestic-only Indian brands (`Swiggy`, `Ola`, `IRCTC`).
*   **Traceable Reasoning**: Combines reasons if a row violates both rules (e.g., `"Statistical Outlier: Amount exceeds 3x account median; USD used for domestic merchant"`).

### C. Gemini Classification (`app/pipeline/llm_service.py`)
*   Extracts transactions marked as `"Uncategorised"`.
*   Splits them into batches of **up to 20** to minimize network roundtrips and stay safely within API rate limits.
*   Instructs Gemini to return a structured JSON mapping transaction indices to allowed categories: `Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, Other`.

### D. Gemini Spending Summary (`app/pipeline/llm_service.py`)
*   Gathers metrics: total spend by currency (INR & USD), top 3 merchants by spend, and anomaly counts.
*   Asks Gemini to write a professional 2-3 sentence analysis narrative and evaluate a `risk_level` (`low`, `medium`, or `high`).

---

## 8. API Specifications & Example requests

### 1. Health Check
*   **GET** `/health`
*   **Response**:
    ```json
    { "status": "healthy" }
    ```
*   **cURL**:
    ```bash
    curl http://localhost:8000/health
    ```

### 2. Upload CSV
*   **POST** `/jobs/upload`
*   **Request**: `multipart/form-data` with key `file`.
*   **Response**:
    ```json
    {
      "job_id": "8902506e-b3be-4fc4-a09c-e366a33ee3c9",
      "status": "pending",
      "filename": "transactions.csv",
      "row_count_raw": 96
    }
    ```
*   **cURL**:
    ```bash
    curl -X POST -F "file=@transactions.csv" http://localhost:8000/jobs/upload
    ```

### 3. Get Job Status
*   **GET** `/jobs/{job_id}/status`
*   **Response**:
    ```json
    {
      "job_id": "8902506e-b3be-4fc4-a09c-e366a33ee3c9",
      "status": "completed",
      "filename": "transactions.csv",
      "row_count_raw": 96,
      "row_count_clean": 95,
      "created_at": "2026-06-20T20:16:57Z",
      "completed_at": "2026-06-20T20:17:15Z",
      "error_message": null
    }
    ```
*   **cURL**:
    ```bash
    curl http://localhost:8000/jobs/8902506e-b3be-4fc4-a09c-e366a33ee3c9/status
    ```

### 4. Get Job Results
*   **GET** `/jobs/{job_id}/results`
*   **Response**: Includes full cleaned transactions, flagged anomalies, category breakdown aggregates, and LLM spending summary.
*   **cURL**:
    ```bash
    curl http://localhost:8000/jobs/8902506e-b3be-4fc4-a09c-e366a33ee3c9/results
    ```

### 5. List Jobs
*   **GET** `/jobs`
*   **Query Params**: `job_status` (optional filter: `pending`, `processing`, `completed`, `failed`)
*   **Response**: List of job records.
*   **cURL**:
    ```bash
    curl http://localhost:8000/jobs?job_status=completed
    ```

---

## 9. Error Handling & Robustness

*   **Pydantic / ORM Schema Mismatches**: Used Pydantic's `Field(validation_alias="id")` for response schemas to cleanly map the database model's primary key (`id`) into API-compliant JSON fields (`job_id`), preventing serialization crashes.
*   **HTTP Exceptions**: All routes use explicit `except HTTPException: raise` blocks before catching generic runtime errors, preventing FastAPI validation and routing exceptions from being swallowed and incorrectly returned as `500 Internal Server Errors`.
*   **LLM API Resilience**: Gemini integrations run inside a `retry_with_backoff` decorator which intercepts network, rate limit, and format issues, executing up to 3 retries with exponential backoff.
*   **Graceful LLM Fallbacks**: If all retries fail:
    *   Classification marks transactions as `llm_failed = True` and keeps category as `"Uncategorised"`.
    *   Summary generation falls back to a preset summary payload.
    *   The overall pipeline job completes successfully instead of crashing mid-run.
*   **Database Rollbacks**: Standardized on single atomic database transactions during worker completion. If bulk saves fail, the transaction is rolled back, the CSV file cleaned, and the Job status committed as `FAILED` with details saved to the `error_message` column.

---

## 10. Design Decisions & Tradeoffs

### Decisions
1.  **FastAPI & Uvicorn**: Lightweight, exceptionally fast, and auto-generates interactive Swagger/OpenAPI documentation.
2.  **Shared Volume File Transfer**: Instead of encoding and passing large CSV payloads through the Redis/Celery broker (which causes latency, increases memory usage, and limits throughput), we write the CSV to local storage volume `uploads/{job_id}.csv` and pass only the `job_id` string via Celery.
3.  **Idempotency Deletes**: If a Celery task is retried or re-executed, any previous transactions or summaries matching the `job_id` are deleted before new ones are inserted. This guarantees that task execution is completely idempotent and prevents primary/unique key violations.
4.  **Vectorized Pandas Logic**: Performing operations using Pandas vectors instead of iterating row-by-row reduces cleaning and anomaly detection execution times significantly.

### Tradeoffs
*   **Shared Volume Requirement**: Using local storage requires that the Web server and Worker containers share a volume. For single-host deployments (docker-compose), this is simple and highly performant. If scaling to a multi-node cluster, local storage must be replaced with shared storage like Amazon S3 or Google Cloud Storage.
*   **Pandas Memory Overhead**: Loading CSV files completely into memory via Pandas is fast but scales with file size. For exceptionally large datasets (e.g. millions of rows), streaming the CSV or chunk-based processing should be implemented.

---

## 11. Future Improvements

1.  **Cloud Storage Integration**: Switch the shared file engine from local storage volumes to AWS S3 or GCP Storage for multi-node worker setups.
2.  **Alembic Database Migrations**: Currently, tables are auto-created on application startup. Implementing Alembic migrations would allow for structured schema updates in production.
3.  **Structured LLM Outputs**: Replace custom JSON validation prompts with Gemini's native structured schemas/Pydantic validation configurations for zero-percent schema deviation guarantees.
4.  **Celery Chords / Task Splitting**: For massive CSV uploads, split the file into smaller chunks, process them in parallel using Celery group tasks, and combine results in a chord callback.
