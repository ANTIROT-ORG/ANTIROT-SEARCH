//! HTTP serving for the Tantivy index (axum).

use anyhow::Result;
use axum::extract::{Query, State};
use axum::routing::get;
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tantivy::{Index, IndexReader, ReloadPolicy};

use crate::schema::{build_schema, Fields};
use crate::search::{search_index, SearchHit};

pub struct AppState {
    pub index: Index,
    pub reader: IndexReader,
    pub fields: Fields,
}

#[derive(Deserialize)]
pub struct SearchParams {
    pub q: String,
    #[serde(default = "default_limit")]
    pub limit: usize,
    pub category: Option<String>,
}

fn default_limit() -> usize {
    20
}

#[derive(Serialize)]
pub struct SearchResponse {
    pub query: String,
    pub results: Vec<SearchHit>,
    pub total_results: usize,
    pub search_type: &'static str,
}

pub fn open_state(index_dir: &str) -> Result<Arc<AppState>> {
    let index = Index::open_in_dir(index_dir)?;
    let reader = index
        .reader_builder()
        .reload_policy(ReloadPolicy::OnCommitWithDelay)
        .try_into()?;
    let (_, fields) = build_schema();
    Ok(Arc::new(AppState {
        index,
        reader,
        fields,
    }))
}

async fn health() -> &'static str {
    "ok"
}

async fn search(
    State(state): State<Arc<AppState>>,
    Query(params): Query<SearchParams>,
) -> Json<SearchResponse> {
    let limit = params.limit.clamp(1, 100);
    let results = search_index(
        &state.index,
        &state.reader,
        &state.fields,
        &params.q,
        limit,
        params.category.as_deref(),
    )
    .unwrap_or_default();

    Json(SearchResponse {
        query: params.q,
        total_results: results.len(),
        results,
        search_type: "tantivy",
    })
}

pub async fn serve(index_dir: &str, port: u16) -> Result<()> {
    let state = open_state(index_dir)?;

    let app = Router::new()
        .route("/health", get(health))
        .route("/search", get(search))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
    println!("ratsearch-index listening on http://127.0.0.1:{port}");
    axum::serve(listener, app).await?;
    Ok(())
}
