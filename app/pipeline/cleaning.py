import os
import re
from typing import Tuple, Dict, Any
import numpy as np
import pandas as pd

def load_csv(file_path: str) -> pd.DataFrame:
    """
    Loads a CSV file into a Pandas DataFrame.

    Args:
        file_path (str): The absolute or relative path to the CSV file.

    Returns:
        pd.DataFrame: Loaded raw transaction data.

    Raises:
        FileNotFoundError: If the file does not exist at the specified path.
        ValueError: If the file cannot be parsed as a CSV.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"CSV file not found at path: {file_path}")
    try:
        # Load the CSV, ensuring all columns are read as strings initially to preserve raw values
        return pd.read_csv(file_path, dtype=str)
    except Exception as e:
        raise ValueError(f"Failed to read CSV file: {str(e)}")

def normalize_dates(series: pd.Series) -> pd.Series:
    """
    Normalizes mixed date formats to pandas Datetime objects.
    Supports formats like DD-MM-YYYY and YYYY/MM/DD.

    Args:
        series (pd.Series): Raw string dates.

    Returns:
        pd.Series: Normalized datetime Series (containing NaT for unparseable dates).
    """
    def parse_single_date(val: Any) -> Any:
        if pd.isna(val):
            return pd.NaT
        val_str = str(val).strip()
        if not val_str or val_str.lower() in ('nan', 'none', 'null', ''):
            return pd.NaT

        # Try specific known formats to avoid day/month swapping issues
        for fmt in ('%d-%m-%Y', '%Y/%m/%d', '%Y-%m-%d', '%d/%m/%Y'):
            try:
                return pd.to_datetime(val_str, format=fmt)
            except (ValueError, TypeError):
                continue

        # Fallback to mixed parsing if none of the formats match
        try:
            return pd.to_datetime(val_str, errors='coerce')
        except Exception:
            return pd.NaT

    return series.apply(parse_single_date)

def normalize_amounts(series: pd.Series) -> pd.Series:
    """
    Cleans amount fields by removing non-numeric characters (except dot and negative signs)
    using regular expressions and converting them to numeric floats.

    Args:
        series (pd.Series): Raw amount strings or numbers.

    Returns:
        pd.Series: Cleaned numeric float Series (containing NaN for unparseable amounts).
    """
    def parse_single_amount(val: Any) -> float:
        if pd.isna(val):
            return np.nan
        if isinstance(val, (int, float)):
            return float(val)
            
        value = str(val).strip()
        if not value or value.lower() in ('nan', 'none', 'null', ''):
            return np.nan

        # Keep only digits, decimal points, and minus signs
        cleaned = re.sub(r'[^0-9.-]', '', value)
        
        try:
            return float(cleaned)
        except ValueError:
            return np.nan

    return series.apply(parse_single_amount)

def clean_transactions(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Cleans and normalizes transaction data.
    
    Cleaning Steps:
    1. Handle missing columns gracefully.
    2. Remove exact duplicate rows.
    3. Normalize date formats to datetime.date.
    4. Normalize amount fields to float.
    5. Drop invalid rows (missing/unparseable date or amount).
    6. Normalize casing and whitespace for status, currency, and categories.
    7. Fill missing categories with 'Uncategorised'.

    Args:
        df (pd.DataFrame): Raw transactions DataFrame.

    Returns:
        Tuple[pd.DataFrame, Dict[str, Any]]:
            - Cleaned DataFrame
            - Metadata dictionary containing:
                - original_rows: int
                - cleaned_rows: int
                - removed_duplicates: int
                - invalid_rows: int
    """
    # 1. Capture original row count
    original_rows = len(df)

    # 2. Handle missing columns gracefully by initializing them with defaults/NaN
    required_columns = {
        'txn_id', 'date', 'merchant', 'amount', 
        'currency', 'status', 'category', 'account_id', 'notes'
    }
    for col in required_columns:
        if col not in df.columns:
            if col == 'category':
                df[col] = 'Uncategorised'
            elif col in ('notes', 'txn_id'):
                df[col] = None
            elif col in ('currency', 'status'):
                df[col] = 'UNKNOWN'
            else:
                # Critical columns become NaN
                df[col] = np.nan

    # Ensure we copy the dataframe to avoid setting with copy warnings
    df = df.copy()

    # 3. Remove exact duplicate rows (comparing all fields)
    duplicate_mask = df.duplicated()
    removed_duplicates = int(duplicate_mask.sum())
    df = df[~duplicate_mask].reset_index(drop=True)

    # 4. Normalize dates
    df['date'] = normalize_dates(df['date'])

    # 5. Normalize amounts
    df['amount'] = normalize_amounts(df['amount'])

    # 6. Drop invalid rows where date or amount is missing/unparseable
    invalid_mask = df['date'].isna() | df['amount'].isna()
    invalid_rows = int(invalid_mask.sum())
    df = df[~invalid_mask].reset_index(drop=True)

    # Convert datetime dates to standard datetime.date objects for database storage compatibility
    df['date'] = df['date'].dt.date

    # 7. Normalize currency (uppercase, stripped, default to UNKNOWN on missing/empty/nan values)
    df['currency'] = df['currency'].fillna('UNKNOWN').astype(str).str.strip().str.upper()
    df.loc[df['currency'].isin(['', 'NAN', 'NONE', 'NULL', 'nan', 'none', 'null']), 'currency'] = 'UNKNOWN'

    # 8. Normalize status (uppercase, stripped, default to UNKNOWN on missing/empty/nan values)
    df['status'] = df['status'].fillna('UNKNOWN').astype(str).str.strip().str.upper()
    df.loc[df['status'].isin(['', 'NAN', 'NONE', 'NULL', 'nan', 'none', 'null']), 'status'] = 'UNKNOWN'

    # 9. Fill missing category or whitespace category with 'Uncategorised'
    df['category'] = df['category'].fillna('Uncategorised').astype(str).str.strip()
    df.loc[df['category'].isin(['', 'nan', 'NAN', 'None', 'NONE', 'null', 'NULL']), 'category'] = 'Uncategorised'

    # Normalize other string fields
    df['merchant'] = df['merchant'].fillna('Unknown').astype(str).str.strip()
    df.loc[df['merchant'].isin(['', 'nan', 'NAN', 'None', 'NONE', 'null', 'NULL']), 'merchant'] = 'Unknown'

    df['account_id'] = df['account_id'].fillna('Unknown').astype(str).str.strip()
    df.loc[df['account_id'].isin(['', 'nan', 'NAN', 'None', 'NONE', 'null', 'NULL']), 'account_id'] = 'Unknown'

    df['notes'] = df['notes'].fillna('').astype(str).str.strip()
    df.loc[df['notes'].isin(['nan', 'NAN', 'None', 'NONE', 'null', 'NULL']), 'notes'] = ''

    df['txn_id'] = df['txn_id'].fillna('').astype(str).str.strip()
    df.loc[df['txn_id'].isin(['nan', 'NAN', 'None', 'NONE', 'null', 'NULL']), 'txn_id'] = ''

    cleaned_rows = len(df)

    metadata = {
        "original_rows": original_rows,
        "cleaned_rows": cleaned_rows,
        "removed_duplicates": removed_duplicates,
        "invalid_rows": invalid_rows
    }

    return df, metadata
