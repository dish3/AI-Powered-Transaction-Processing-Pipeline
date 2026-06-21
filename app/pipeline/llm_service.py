import json
import time
import random
import logging
import functools
from typing import List, Dict, Any
import google.generativeai as genai
from google.api_core.exceptions import GoogleAPICallError
from app.config import settings

logger = logging.getLogger(__name__)

def configure_gemini():
    """
    Configures the google-generativeai package with the API key from settings.
    """
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        logger.warning("GEMINI_API_KEY is not set. Gemini API operations will fail.")
    genai.configure(api_key=api_key)

class RetryableLLMError(Exception):
    """Exception raised for LLM errors that are eligible for retry."""
    pass

def retry_with_backoff(max_retries: int = 3, initial_delay: float = 1.0, backoff_factor: float = 2.0):
    """
    Decorator for retrying a function with exponential backoff on RetryableLLMError.
    
    Args:
        max_retries (int): Maximum number of retry attempts.
        initial_delay (float): Delay before the first retry in seconds.
        backoff_factor (float): Multiplier applied to the delay after each retry.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except RetryableLLMError as e:
                    if attempt == max_retries:
                        logger.error(f"LLM call failed after {max_retries} retries: {str(e)}")
                        raise e
                    
                    # Add simple jitter (0-10% of delay) to prevent thundering herd
                    sleep_time = delay + random.uniform(0, 0.1 * delay)
                    logger.warning(
                        f"Retryable error encountered: {str(e)}. "
                        f"Retrying in {sleep_time:.2f} seconds (attempt {attempt + 1}/{max_retries})..."
                    )
                    time.sleep(sleep_time)
                    delay *= backoff_factor
        return wrapper
    return decorator

class LLMService:
    def __init__(self, model_name: str = "gemini-1.5-flash"):
        """
        Initializes the LLM Service and Configures the Generative Model.
        """
        configure_gemini()
        self.model_name = model_name
        self.allowed_categories = {
            "Food", "Shopping", "Travel", "Transport", 
            "Utilities", "Cash Withdrawal", "Entertainment", "Other"
        }

    @retry_with_backoff(max_retries=3)
    def _classify_batch_with_retry(self, batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Internal method to classify a single batch of transactions with retry logic.
        """
        prompt = (
            "You are an expert financial transaction classification assistant. "
            "Your task is to classify a batch of uncategorized financial transactions "
            "into exactly one of these allowed categories:\n"
            f"{', '.join(sorted(self.allowed_categories))}\n\n"
            "Analyze the merchant name, amount, and notes of each transaction to select the best fit. "
            "Use 'Other' if a transaction does not fit any of the categories.\n\n"
            "Format your output strictly as a JSON object matching this schema:\n"
            "{\n"
            '  "classifications": [\n'
            '    {"id": "transaction_id_passed_in_input", "category": "CategoryName"}\n'
            "  ]\n"
            "}\n\n"
            f"Transactions to classify:\n{json.dumps(batch, default=str)}"
        )

        try:
            model = genai.GenerativeModel(self.model_name)
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            text_response = response.text
        except GoogleAPICallError as e:
            raise RetryableLLMError(f"Gemini API error during classification: {str(e)}") from e
        except Exception as e:
            raise RetryableLLMError(f"Unexpected network or API failure: {str(e)}") from e

        # Validate JSON response using json.loads
        try:
            data = json.loads(text_response)
        except json.JSONDecodeError as e:
            raise RetryableLLMError(f"Gemini returned invalid JSON: {text_response[:300]}") from e

        # Validate schema keys
        if not isinstance(data, dict) or "classifications" not in data:
            raise RetryableLLMError(f"Invalid schema: Missing 'classifications' key in: {data}")

        classifications = data["classifications"]
        if not isinstance(classifications, list):
            raise RetryableLLMError(f"Invalid schema: 'classifications' is not a list in: {data}")

        # Build map of input IDs to check completeness
        input_ids = {str(item["id"]) for item in batch}
        validated_results = []

        for item in classifications:
            if not isinstance(item, dict) or "id" not in item or "category" not in item:
                raise RetryableLLMError(f"Invalid classification item structure: {item}")
            
            item_id = str(item["id"])
            if item_id not in input_ids:
                logger.warning(f"Gemini returned ID {item_id} which was not in the input batch. Ignoring.")
                continue

            category = item["category"].strip()
            # Normalize to match allowed category casing
            matched_category = "Other"
            for allowed in self.allowed_categories:
                if category.lower() == allowed.lower():
                    matched_category = allowed
                    break

            validated_results.append({
                "id": item_id,
                "llm_category": matched_category,
                "llm_raw_response": text_response,
                "llm_failed": False
            })

        # Ensure all input transactions are accounted for
        returned_ids = {res["id"] for res in validated_results}
        missing_ids = input_ids - returned_ids

        if missing_ids:
            # If some IDs are missing from LLM response, raise error to trigger retry
            raise RetryableLLMError(f"LLM missed classifying the following transaction IDs: {missing_ids}")

        return validated_results

    def classify_uncategorized_transactions(self, transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Classifies uncategorized transactions in batches of up to 20.
        
        Args:
            transactions (list[dict]): List of transaction dicts, each with 'id', 'merchant', 'amount', 'currency', 'notes'.

        Returns:
            list[dict]: List of classification results with keys:
                        'id', 'llm_category', 'llm_raw_response', 'llm_failed'.
        """
        if not transactions:
            return []

        results = []
        batch_size = 20

        # Process in batches of 20
        for i in range(0, len(transactions), batch_size):
            batch = transactions[i:i + batch_size]
            # Strip out only fields needed for classification to reduce tokens
            classification_input = [
                {
                    "id": str(tx.get("id")),
                    "merchant": tx.get("merchant", "Unknown"),
                    "amount": tx.get("amount", 0.0),
                    "currency": tx.get("currency", "INR"),
                    "notes": tx.get("notes", "")
                }
                for tx in batch
            ]

            logger.info(f"Classifying batch of {len(classification_input)} transactions...")
            
            try:
                batch_results = self._classify_batch_with_retry(classification_input)
                results.extend(batch_results)
            except Exception as e:
                # If all retries failed, handle gracefully without crashing the whole pipeline
                logger.error(f"Failed to classify batch after all retries: {str(e)}")
                for tx in batch:
                    results.append({
                        "id": str(tx.get("id")),
                        "llm_category": "Uncategorised",
                        "llm_raw_response": f"Failed after retries. Error: {str(e)}",
                        "llm_failed": True
                    })

        return results

    @retry_with_backoff(max_retries=3)
    def _generate_summary_with_retry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Internal method to generate transaction summary narrative and risk with retry logic.
        """
        prompt = (
            "You are a professional financial analyst. "
            "Analyze the following aggregated financial transaction report:\n"
            f"{json.dumps(payload, default=str)}\n\n"
            "Provide a professional 2-3 sentence spending narrative summarizing the trends, major spend categories, or anomalies. "
            "Also, evaluate the risk level (low, medium, or high) based on anomalies, USD domestic usage, and transaction patterns.\n\n"
            "Format your output strictly as a JSON object matching this schema:\n"
            "{\n"
            '  "narrative": "2-3 sentence analysis narrative here",\n'
            '  "risk_level": "low|medium|high"\n'
            "}\n"
        )

        try:
            model = genai.GenerativeModel(self.model_name)
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            text_response = response.text
        except GoogleAPICallError as e:
            raise RetryableLLMError(f"Gemini API error during summary generation: {str(e)}") from e
        except Exception as e:
            raise RetryableLLMError(f"Unexpected network or API failure during summary: {str(e)}") from e

        # Validate JSON response
        try:
            data = json.loads(text_response)
        except json.JSONDecodeError as e:
            raise RetryableLLMError(f"Gemini returned invalid summary JSON: {text_response[:300]}") from e

        # Validate keys
        if not isinstance(data, dict) or "narrative" not in data or "risk_level" not in data:
            raise RetryableLLMError(f"Invalid summary schema keys: {data}")

        risk = str(data["risk_level"]).strip().lower()
        if risk not in ("low", "medium", "high"):
            logger.warning(f"Invalid risk_level '{risk}' returned. Defaulting to 'medium'.")
            data["risk_level"] = "medium"

        return data

    def generate_summary(self, summary_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generates narrative summary and risk assessment for a job.
        
        Args:
            summary_payload (dict): Aggregate values containing:
                                    'total_spend_inr', 'total_spend_usd', 'top_merchants', 'anomaly_count'.

        Returns:
            dict: JSON summary with 'narrative' and 'risk_level'.
        """
        logger.info("Generating spending narrative and risk assessment via Gemini...")
        try:
            return self._generate_summary_with_retry(summary_payload)
        except Exception as e:
            logger.error(f"Failed to generate summary after all retries: {str(e)}")
            # Fallback gracefully
            return {
                "narrative": "Could not generate spending narrative due to LLM service failure.",
                "risk_level": "medium"
            }
