"""BC3PL Provider Pipeline Dashboard – Streamlit frontend."""

import os
import subprocess
import streamlit as st
import pandas as pd

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

STAGE_FILES = {
    "Raw": "providers_raw.jsonl",
    "Enriched": "providers_enriched.jsonl",
    "Verified": "providers_verified.jsonl",
    "Audited": "providers_audited.jsonl",
    "Scored": "providers_scored.jsonl",
    "Call Sheet": "call_sheet.csv",
}

DISPLAY_COLUMNS = [
    "company",
    "provider_category",
    "verification_status",
    "priority",
    "lead_fit_score",
    "city",
    "phone",
    "website",
    "services",
    "evidence_notes",
    "audit_notes",
    "recommended_call_angle",
    "evidence_urls",
]


def file_exists(filename: str) -> bool:
    return os.path.isfile(os.path.join(OUTPUT_DIR, filename))


def load_csv(filename: str) -> pd.DataFrame | None:
    path = os.path.join(OUTPUT_DIR, filename)
    if os.path.isfile(path):
        return pd.read_csv(path)
    return None


def available_columns(df: pd.DataFrame, cols: list[str]) -> list[str]:
    return [c for c in cols if c in df.columns]


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    with st.sidebar:
        st.header("Filters")
        if "verification_status" in df.columns:
            options = sorted(df["verification_status"].dropna().unique().tolist())
            selected = st.multiselect("Verification Status", options)
            if selected:
                df = df[df["verification_status"].isin(selected)]
        if "provider_category" in df.columns:
            options = sorted(df["provider_category"].dropna().unique().tolist())
            selected = st.multiselect("Provider Category", options)
            if selected:
                df = df[df["provider_category"].isin(selected)]
        if "priority" in df.columns:
            options = sorted(df["priority"].dropna().unique().tolist())
            selected = st.multiselect("Priority", options)
            if selected:
                df = df[df["priority"].isin(selected)]
        if "city" in df.columns:
            options = sorted(df["city"].dropna().unique().tolist())
            selected = st.multiselect("City", options)
            if selected:
                df = df[df["city"].isin(selected)]
    return df


def run_command(label: str, cmd: list[str]) -> None:
    st.info(f"Running: `{' '.join(cmd)}`")
    with st.spinner("Pipeline running..."):
        result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        st.success("Command completed successfully.")
    else:
        st.error(f"Command exited with code {result.returncode}")
    if result.stdout:
        st.text_area(f"{label} – stdout", result.stdout, height=200)
    if result.stderr:
        st.text_area(f"{label} – stderr", result.stderr, height=200)


# ─── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="BC3PL Provider Pipeline", layout="wide")
st.title("BC3PL Provider Pipeline Dashboard")

# ─── Stage status ──────────────────────────────────────────────────────────────

st.header("Pipeline Stage Status")
cols = st.columns(len(STAGE_FILES))
for col, (stage, filename) in zip(cols, STAGE_FILES.items()):
    exists = file_exists(filename)
    col.metric(stage, "Ready" if exists else "Missing")

# ─── Counts ────────────────────────────────────────────────────────────────────

st.header("Summary Counts")

scored_df = load_csv("providers_scored.csv")
audited_df = load_csv("providers_audited.csv")
verified_df = load_csv("providers_verified.csv")
call_sheet_df = load_csv("call_sheet.csv")

# Use audited if available, otherwise verified for status counts
status_source = audited_df if audited_df is not None else verified_df

count_cols = st.columns(5)
total = len(scored_df) if scored_df is not None else (len(verified_df) if verified_df is not None else 0)
count_cols[0].metric("Total Providers", total)

if status_source is not None and "verification_status" in status_source.columns:
    counts = status_source["verification_status"].value_counts()
    count_cols[1].metric("Approved", int(counts.get("approved", 0)))
    count_cols[2].metric("Review", int(counts.get("review", 0)))
    count_cols[3].metric("Rejected", int(counts.get("rejected", 0)))

count_cols[4].metric("Call Sheet Rows", len(call_sheet_df) if call_sheet_df is not None else 0)

# ─── Tables ────────────────────────────────────────────────────────────────────

st.header("Provider Data")

# Build a combined dataframe for filtering (prefer scored since it has all columns)
main_df = scored_df if scored_df is not None else verified_df
if main_df is not None:
    filtered_df = apply_filters(main_df.copy())
else:
    filtered_df = None

tab_names = ["Verified", "Audited", "Scored", "Call Sheet"]
tabs = st.tabs(tab_names)

with tabs[0]:
    if verified_df is not None:
        show_cols = available_columns(verified_df, DISPLAY_COLUMNS)
        if filtered_df is not None and "company" in verified_df.columns:
            mask = verified_df["company"].isin(filtered_df["company"]) if filtered_df is not None else pd.Series([True] * len(verified_df))
            st.dataframe(verified_df.loc[mask, show_cols], use_container_width=True)
        else:
            st.dataframe(verified_df[show_cols], use_container_width=True)
    else:
        st.info("providers_verified.csv not found.")

with tabs[1]:
    if audited_df is not None:
        show_cols = available_columns(audited_df, DISPLAY_COLUMNS)
        if filtered_df is not None and "company" in audited_df.columns:
            mask = audited_df["company"].isin(filtered_df["company"])
            st.dataframe(audited_df.loc[mask, show_cols], use_container_width=True)
        else:
            st.dataframe(audited_df[show_cols], use_container_width=True)
    else:
        st.info("providers_audited.csv not found.")

with tabs[2]:
    if scored_df is not None:
        show_cols = available_columns(scored_df, DISPLAY_COLUMNS)
        if filtered_df is not None:
            st.dataframe(filtered_df[show_cols], use_container_width=True)
        else:
            st.dataframe(scored_df[show_cols], use_container_width=True)
    else:
        st.info("providers_scored.csv not found.")

with tabs[3]:
    if call_sheet_df is not None:
        st.dataframe(call_sheet_df, use_container_width=True)
    else:
        st.info("call_sheet.csv not found.")

# ─── Run pipeline ──────────────────────────────────────────────────────────────

st.header("Run Pipeline")

col1, col2, col3 = st.columns(3)

with col1:
    if st.button("Quick Run (no OpenAI)"):
        run_command(
            "Quick Run",
            ["python", "-m", "provider_pipeline", "run-all", "--max-results-per-query", "3", "--no-openai"],
        )

with col2:
    if st.button("With Audit (limit 10)"):
        run_command(
            "Audit Run (10)",
            ["python", "-m", "provider_pipeline", "run-all", "--max-results-per-query", "3", "--audit", "--audit-limit", "10"],
        )

with col3:
    if st.button("Full Run + Audit (limit 25)"):
        run_command(
            "Full Run + Audit",
            ["python", "-m", "provider_pipeline", "run-all", "--max-results-per-query", "10", "--audit", "--audit-limit", "25"],
        )
