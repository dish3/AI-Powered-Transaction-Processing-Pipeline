import os
import csv
import io
import uuid
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status as http_status
from sqlalchemy.orm import Session
from app import models, schemas
from app.database import get_db
from app.config import settings
from app.models import JobStatus
from app.worker import process_transaction_job

router = APIRouter(prefix="/jobs", tags=["Jobs"])
logger = logging.getLogger(__name__)

@router.post("/upload", response_model=schemas.JobUploadResponse, status_code=http_status.HTTP_202_ACCEPTED)
async def upload_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # Validate file extension case-insensitively
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only CSV files are allowed."
        )

    try:
        contents = await file.read()
        decoded = contents.decode("utf-8")
        
        # Parse CSV to count raw rows
        csv_reader = csv.reader(io.StringIO(decoded))
        header = next(csv_reader, None)
        
        if header is None:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Empty CSV file uploaded."
            )
            
        row_count = sum(1 for row in csv_reader if row)
    except HTTPException:
        raise
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Invalid CSV file encoding. Please upload a UTF-8 encoded CSV file."
        )
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse CSV file: {str(e)}"
        )

    # Create Job record in the database
    job = models.Job(
        filename=file.filename,
        status=JobStatus.PENDING,
        row_count_raw=row_count
    )
    
    try:
        db.add(job)
        db.commit()
        db.refresh(job)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while creating job record: {str(e)}"
        )

    # Save the file locally to uploads/{job_id}.csv
    upload_path = os.path.join(settings.UPLOAD_DIR, f"{job.id}.csv")
    try:
        with open(upload_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        # Cleanup job record on file write failure
        db.delete(job)
        db.commit()
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save uploaded file locally: {str(e)}"
        )

    # Enqueue Celery task with job_id string (run synchronously for testing)
    try:
        process_transaction_job(str(job.id))
    except Exception as e:
        # Cleanup job and file if queueing fails
        if os.path.exists(upload_path):
            try:
                os.remove(upload_path)
            except Exception:
                pass
        db.delete(job)
        db.commit()
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to queue job for processing: {str(e)}"
        )

    return job

@router.get("", response_model=List[schemas.JobListEntry])
def list_jobs(
    job_status: Optional[JobStatus] = None,
    db: Session = Depends(get_db)
):
    try:
        query = db.query(models.Job)
        if job_status:
            query = query.filter(models.Job.status == job_status)
        jobs = query.order_by(models.Job.created_at.desc()).all()
        return jobs
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve jobs list: {str(e)}"
        )

@router.get("/{job_id}/status", response_model=schemas.JobStatusResponse)
def get_job_status(
    job_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID {job_id} not found."
        )
    return job

@router.get("/{job_id}/results", response_model=schemas.JobResultsResponse)
def get_job_results(
    job_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    # Retrieve job
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID {job_id} not found."
        )

    # Build results response - return job_id explicitly instead of id
    response_data = {
        "job_id": job.id,
        "status": job.status,
        "summary": None,
        "category_breakdown": {},
        "anomalies": [],
        "transactions": []
    }

    # If the job isn't completed yet, return status and empty lists
    if job.status != JobStatus.COMPLETED:
        return response_data

    try:
        # Retrieve all transactions for the job
        transactions = db.query(models.Transaction).filter(models.Transaction.job_id == job_id).all()
        
        # Build category breakdown: Dict[str, Dict[str, float]] -> {currency: {category: sum_amount}}
        category_breakdown = {}
        anomalies_list = []
        transactions_list = []

        for tx in transactions:
            curr = tx.currency
            cat = tx.category
            amount_val = float(tx.amount)

            if curr not in category_breakdown:
                category_breakdown[curr] = {}
            category_breakdown[curr][cat] = category_breakdown[curr].get(cat, 0.0) + amount_val

            # Add to transactions list
            transactions_list.append(tx)

            # Add to anomalies list if flagged
            if tx.is_anomaly:
                anomalies_list.append({
                    "txn_id": tx.txn_id,
                    "date": tx.date,
                    "merchant": tx.merchant,
                    "amount": amount_val,
                    "currency": tx.currency,
                    "account_id": tx.account_id,
                    "anomaly_reason": tx.anomaly_reason
                })

        response_data["category_breakdown"] = category_breakdown
        response_data["anomalies"] = anomalies_list
        response_data["transactions"] = transactions_list

        # Load LLM Narrative Summary
        if job.summary:
            summary_orm = job.summary
            response_data["summary"] = {
                "total_spend_inr": float(summary_orm.total_spend_inr),
                "total_spend_usd": float(summary_orm.total_spend_usd),
                "top_merchants": summary_orm.top_merchants,  # JSON list of MerchantSpend
                "anomaly_count": summary_orm.anomaly_count,
                "narrative": summary_orm.narrative,
                "risk_level": summary_orm.risk_level
            }

        return response_data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error compiling job results: {str(e)}"
        )
