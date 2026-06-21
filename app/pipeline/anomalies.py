import pandas as pd
import numpy as np

def compute_account_medians(df: pd.DataFrame) -> pd.Series:
    """
    Computes the median transaction amount for each unique account_id.

    Args:
        df (pd.DataFrame): Normalized transactions DataFrame containing 'account_id' and 'amount'.

    Returns:
        pd.Series: A Series mapping 'account_id' to its median transaction amount.
    """
    if df.empty or 'account_id' not in df.columns or 'amount' not in df.columns:
        return pd.Series(dtype=float)
    
    # Group by account_id and calculate the median of transaction amount
    return df.groupby('account_id')['amount'].median()

def detect_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detects and flags statistical outliers and currency mismatch anomalies.
    
    Anomaly Rules:
    1. Statistical Outlier: Transaction amount exceeds 3x the account's median.
    2. Currency Mismatch: Currency is USD but merchant is a domestic Indian service
       (Swiggy, Ola, or IRCTC).

    Args:
        df (pd.DataFrame): Cleaned transactions DataFrame.

    Returns:
        pd.DataFrame: DataFrame with populated 'is_anomaly' and 'anomaly_reason' columns.
    """
    # Create a copy to prevent modifying the original dataframe in-place
    df = df.copy()

    # Initialize anomaly columns if they are not already present
    if 'is_anomaly' not in df.columns:
        df['is_anomaly'] = False
    if 'anomaly_reason' not in df.columns:
        df['anomaly_reason'] = None

    # Return immediately if the dataframe is empty
    if df.empty:
        return df

    # 1. Compute and map account medians for outlier detection
    medians = compute_account_medians(df)
    medians_map = df['account_id'].map(medians)

    # Flag outliers: amount > 3 * account_median. Handle null medians safely.
    outlier_mask = medians_map.notna() & (df['amount'] > (3 * medians_map))

    # 2. Flag currency mismatch (currency is USD for domestic Indian merchants). Null-safe.
    domestic_merchants = {'swiggy', 'ola', 'irctc'}
    currency_mask = (df['currency'] == 'USD') & df['merchant'].fillna('').str.lower().isin(domestic_merchants)

    # 3. Vectorized assembly of anomaly reasons
    reasons = pd.Series("", index=df.index, dtype=str)
    
    # Populate outlier reasons
    outlier_reason = "Statistical Outlier: Amount exceeds 3x account median"
    reasons.loc[outlier_mask] = outlier_reason

    # Populate currency mismatch reasons, handling overlaps
    mismatch_reason = "USD used for domestic merchant"
    
    # Case A: Both outlier and currency mismatch apply
    overlap_mask = outlier_mask & currency_mask
    reasons.loc[overlap_mask] += "; " + mismatch_reason

    # Case B: Only currency mismatch applies
    mismatch_only_mask = (~outlier_mask) & currency_mask
    reasons.loc[mismatch_only_mask] = mismatch_reason

    # Update DataFrame columns using the masks
    anomaly_mask = outlier_mask | currency_mask
    df.loc[anomaly_mask, 'is_anomaly'] = True
    df.loc[anomaly_mask, 'anomaly_reason'] = reasons.loc[anomaly_mask]

    return df
