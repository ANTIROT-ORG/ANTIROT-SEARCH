//! ratsearch-index — Tantivy serving index for RatSearch.
//!
//! Classical BM25 + the shared ranking blend. No ML.
//!
//!   ratsearch-index sync   --db ../search/data/local.db --index ../search/data/tantivy
//!   ratsearch-index search --index ../search/data/tantivy --query "climate change"
//!   ratsearch-index serve  --index ../search/data/tantivy --port 8090

mod schema;
mod search;
mod server;
mod sync;

use anyhow::Result;
use clap::{Parser, Subcommand};
use schema::build_schema;
use search::search_index;
use tantivy::Index;

#[derive(Parser)]
#[command(name = "ratsearch-index", version, about = "RatSearch Tantivy serving index")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Rebuild the Tantivy index from the SQLite golden layer
    Sync {
        #[arg(long)]
        db: String,
        #[arg(long)]
        index: String,
    },
    /// Run a search against the index
    Search {
        #[arg(long)]
        index: String,
        #[arg(long)]
        query: String,
        #[arg(long, default_value_t = 20)]
        limit: usize,
        #[arg(long)]
        category: Option<String>,
    },
    /// Serve the index over HTTP
    Serve {
        #[arg(long)]
        index: String,
        #[arg(long, default_value_t = 8090)]
        port: u16,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Sync { db, index } => {
            let stats = sync::sync_from_sqlite(&db, &index)?;
            println!("{}", serde_json::to_string_pretty(&stats)?);
        }
        Commands::Search {
            index,
            query,
            limit,
            category,
        } => {
            let index = Index::open_in_dir(&index)?;
            let reader = index.reader()?;
            let (_, fields) = build_schema();
            let hits = search_index(
                &index,
                &reader,
                &fields,
                &query,
                limit,
                category.as_deref(),
            )?;
            println!("{}", serde_json::to_string_pretty(&hits)?);
        }
        Commands::Serve { index, port } => {
            server::serve(&index, port).await?;
        }
    }

    Ok(())
}
