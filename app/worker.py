import os
import uuid
import logging
from datetime import datetime, timezone
import pandas as pd
from celery import Celery
from app import models
from app.config import settings
from app.database import SessionLocal
from app.pipeline.cleaning import load_csv, clean_transactions
from app.pipeline.anomalies import detect_anomalies
from app.pipeline.llm_service import LLMService

# Configure logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Celery application
celery = Celery(
    "tasks",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL
)

# Celery configuration overrides
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

# Enable Celery autodiscovery of tasks
celery.autodiscover_tasks()

@celery.task(name="app.worker.process_transaction_job")
def process_transaction_job(job_id: str) -> None:
    """
    Background Celery task that coordinates the entire data processing pipeline:
    1. Loads the Job from PostgreSQL database.
    2. Updates status to PROCESSING.
    3. Reads raw uploaded CSV file.
    4. Cleans and normalizes fields using Pandas.
    5. Flags statistical and rule-based anomalies.
    6. Batches and classifies uncategorized rows via Gemini LLM.
    7. Generates summary metrics, narrative, and risk level via Gemini LLM.
    8. Bulk inserts cleaned records and summary report to database.
    9. Finalizes Job status to COMPLETED (or FAILED on error).
    """
    logger.info(f"Starting background processing for Job ID: {job_id}")
    
    # Establish database session
    db = SessionLocal()
    
    try:
        # 1. Load Job record
        job_uuid = uuid.UUID(job_id)
        job = db.query(models.Job).filter(models.Job.id == job_uuid).first()
        
        if not job:
            logger.error(f"Job {job_id} not found in database.")
            return

        # 2. Update status to PROCESSING (Immediate commit for polling UI visibility)
        job.status = models.JobStatus.PROCESSING
        db.commit()
        logger.info(f"Job {job_id} status updated to PROCESSING.")

        # 3. Resolve upload CSV file path
        csv_filename = f"{job_id}.csv"
        csv_path = os.path.join(settings.UPLOAD_DIR, csv_filename)
        
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"Uploaded CSV file not found at: {csv_path}")

        # 4. Load CSV using pipeline load_csv
        logger.info(f"Loading CSV file for job {job_id}...")
        df_raw = load_csv(csv_path)

        # 5. Clean transactions using pipeline clean_transactions
        logger.info(f"Cleaning transactions for job {job_id}...")
        df_clean, metadata = clean_transactions(df_raw)

        # Initialize LLM fields in dataframe
        df_clean['llm_category'] = None
        df_clean['llm_raw_response'] = None
        df_clean['llm_failed'] = False

        # 6. Detect anomalies
        logger.info(f"Detecting anomalies for job {job_id}...")
        df_anomalous = detect_anomalies(df_clean)

        # Instantiate Gemini LLM Service
        llm_service = LLMService()

        # 7. Run LLM classification on Uncategorised rows
        uncat_mask = df_anomalous['category'] == 'Uncategorised'
        if uncat_mask.any():
            logger.info(f"Found {uncat_mask.sum()} uncategorized transactions. Initiating batch LLM classification...")
            
            # Map DataFrame index as a unique ID string for LLM tracking
            uncat_indices = df_anomalous[uncat_mask].index.tolist()
            llm_input = []
            for idx in uncat_indices:
                row = df_anomalous.loc[idx]
                llm_input.append({
                    "id": str(idx),
                    "merchant": row.get("merchant", "Unknown"),
                    "amount": row.get("amount", 0.0),
                    "currency": row.get("currency", "INR"),
                    "notes": row.get("notes", "")
                })

            # Call LLM Service batch classification
            classifications = llm_service.classify_uncategorized_transactions(llm_input)

            # Map categories and details back to DataFrame
            for item in classifications:
                idx = int(item["id"])
                df_anomalous.at[idx, "llm_category"] = item["llm_category"]
                df_anomalous.at[idx, "llm_raw_response"] = item["llm_raw_response"]
                df_anomalous.at[idx, "llm_failed"] = item["llm_failed"]
                
                # If LLM classified it successfully, update the category too
                if not item["llm_failed"] and item["llm_category"] != "Uncategorised":
                    df_anomalous.at[idx, "category"] = item["llm_category"]

        # 8. Compute spending summary metrics
        logger.info(f"Computing spend statistics for job {job_id}...")
        
        # Calculate totals on successful transactions (default to all if no success statuses exist)
        success_mask = df_anomalous['status'] == 'SUCCESS'
        if not success_mask.any():
            success_mask = pd.Series(True, index=df_anomalous.index)

        total_spend_inr = float(df_anomalous.loc[success_mask & (df_anomalous['currency'] == 'INR'), 'amount'].sum())
        total_spend_usd = float(df_anomalous.loc[success_mask & (df_anomalous['currency'] == 'USD'), 'amount'].sum())
        
        # Calculate Top 3 merchants by spend
        merchant_stats = df_anomalous.groupby('merchant').agg(
            spend=('amount', 'sum'),
            count=('amount', 'size')
        ).reset_index()
        top_merchants_df = merchant_stats.sort_values(by='spend', ascending=False).head(3)
        top_merchants = [
            {
                "merchant": str(row['merchant']),
                "spend": float(row['spend']),
                "count": int(row['count'])
            }
            for _, row in top_merchants_df.iterrows()
        ]
        
        # Calculate total anomalies
        anomaly_count = int(df_anomalous['is_anomaly'].sum())

        # Generate LLM spend narrative summary
        summary_payload = {
            "total_spend_inr": total_spend_inr,
            "total_spend_usd": total_spend_usd,
            "top_merchants": top_merchants,
            "anomaly_count": anomaly_count
        }
        
        logger.info(f"Generating spending summary report via Gemini for job {job_id}...")
        llm_summary = llm_service.generate_summary(summary_payload)

        # 9. Build persistence objects
        transaction_objects = []
        for _, row in df_anomalous.iterrows():
            tx = models.Transaction(
                job_id=job.id,
                txn_id=row.get("txn_id") if row.get("txn_id") else None,
                date=row["date"],
                merchant=row["merchant"],
                amount=row["amount"],
                currency=row["currency"],
                status=row["status"],
                category=row["category"],
                account_id=row["account_id"],
                notes=row.get("notes") if row.get("notes") else None,
                is_anomaly=bool(row["is_anomaly"]),
                anomaly_reason=row.get("anomaly_reason") if row.get("anomaly_reason") else None,
                llm_category=row.get("llm_category") if row.get("llm_category") else None,
                llm_raw_response=row.get("llm_raw_response") if row.get("llm_raw_response") else None,
                llm_failed=bool(row.get("llm_failed", False))
            )
            transaction_objects.append(tx)

        # Create JobSummary object with fallbacks to avoid KeyError
        job_summary = models.JobSummary(
            job_id=job.id,
            total_spend_inr=total_spend_inr,
            total_spend_usd=total_spend_usd,
            top_merchants=top_merchants,
            anomaly_count=anomaly_count,
            narrative=llm_summary.get("narrative", "Could not generate spending narrative."),
            risk_level=llm_summary.get("risk_level", "medium")
        )

        # 10. Atomic DB Persistence block: Deletes, bulk saves, and job status finalized together
        logger.info(f"Persisting data atomically for job {job_id}...")
        try:
            # Delete any existing transactions and summaries from previous runs to ensure idempotency
            db.query(models.Transaction).filter(models.Transaction.job_id == job.id).delete()
            db.query(models.JobSummary).filter(models.JobSummary.job_id == job.id).delete()
            db.flush()

            # Save the transaction mappings and job summary
            db.bulk_save_objects(transaction_objects)
            db.add(job_summary)

            # Update job metadata to complete
            job.row_count_clean = int(metadata["cleaned_rows"])
            job.status = models.JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc)
            
            db.commit()
            logger.info(f"Job {job_id} successfully completed and saved atomically.")
        except Exception:
            db.rollback()
            raise

    except Exception as e:
        logger.exception(f"Unhandled error occurred while processing Job {job_id}: {str(e)}")
        # Rollback active database transaction to prevent corrupt state
        db.rollback()
        
        # Log failure to Job record in database
        try:
            # Re-fetch job object to ensure session handles it correctly post-rollback
            failed_job = db.query(models.Job).filter(models.Job.id == uuid.UUID(job_id)).first()
            if failed_job:
                failed_job.status = models.JobStatus.FAILED
                failed_job.error_message = f"Pipeline execution failed: {str(e)}"
                failed_job.completed_at = datetime.now(timezone.utc)
                db.commit()
                logger.info(f"Job {job_id} status successfully marked as FAILED in DB.")
        except Exception as db_err:
            logger.error(f"Failed to record job failure in database: {str(db_err)}")
        
        # Preserve original traceback
        raise
    finally:
        # Close database session
        db.close()
