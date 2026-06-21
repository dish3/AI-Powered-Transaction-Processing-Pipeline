from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import engine, Base
from app.routes.jobs import router as jobs_router
from app.schemas import HealthResponse

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Auto-create tables in the database
    Base.metadata.create_all(bind=engine)
    yield
    # Shutdown: Clean up resources if needed

app = FastAPI(
    title="AI-Powered Transaction Processing Pipeline",
    description="Asynchronous CSV parser, transaction cleaner, statistical anomaly detector, and LLM classification/narrative generator.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration
origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health endpoint
@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health_check():
    return {"status": "healthy"}

# Include routes
app.include_router(jobs_router)
