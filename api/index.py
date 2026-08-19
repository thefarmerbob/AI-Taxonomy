from file_classification import build_app  # type: ignore

# FastAPI app with the Gradio interface mounted at the root path,
# plus the inline PDF preview route.
app = build_app()
