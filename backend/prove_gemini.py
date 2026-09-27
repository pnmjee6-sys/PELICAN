from __future__ import annotations

import json
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def test_gemini_extraction_and_embedding() -> dict[str, object]:
    """
    Tests live Gemini API extraction candidate and 1536-dim embedding model.
    Confirms exact working model IDs without printing credentials.
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_gemini_api_key":
        return {
            "ok": False,
            "status": "not_configured",
            "message": "GEMINI_API_KEY is not configured in .env",
        }

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    extraction_model = os.getenv("GEMINI_EXTRACTION_MODEL", "gemini-2.5-flash")
    embedding_model = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
    target_dims = int(os.getenv("GEMINI_EMBEDDING_DIMS", "1536"))

    report: dict[str, object] = {
        "extraction_candidate": extraction_model,
        "embedding_model": embedding_model,
        "target_embedding_dims": target_dims,
    }

    # 1. Test extraction candidate
    try:
        extraction_prompt = (
            "Extract any durable user facts or preferences from this message into a single concise sentence: "
            "'For my frontend projects, I strongly prefer TypeScript examples.'"
        )
        response = client.models.generate_content(
            model=extraction_model,
            contents=extraction_prompt,
        )
        extracted_text = response.text.strip() if response.text else ""
        report["extraction"] = {
            "ok": bool(extracted_text),
            "model_used": extraction_model,
            "sample_output": extracted_text,
        }
    except Exception as exc:
        report["extraction"] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }

    # 2. Test 1536-dimension embedding
    try:
        embed_config = types.EmbedContentConfig(output_dimensionality=target_dims) if target_dims else None
        embed_response = client.models.embed_content(
            model=embedding_model,
            contents="Context Passport test embedding memory content",
            config=embed_config,
        )
        embeddings = embed_response.embeddings
        dim_count = len(embeddings[0].values) if embeddings and embeddings[0].values else 0
        report["embedding"] = {
            "ok": dim_count == target_dims,
            "model_used": embedding_model,
            "returned_dimensions": dim_count,
            "matches_target_dims": dim_count == target_dims,
        }
    except Exception as exc:
        report["embedding"] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }

    report["ok"] = (
        bool(report.get("extraction", {}).get("ok"))
        and bool(report.get("embedding", {}).get("ok"))
    )
    return report


if __name__ == "__main__":
    result = test_gemini_extraction_and_embedding()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)
