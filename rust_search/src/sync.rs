//! SQLite → Tantivy sync (full rebuild; swap in place).

use anyhow::Result;
use rusqlite::Connection;
use serde::Serialize;
use std::fs;
use std::path::Path;
use tantivy::doc;
use tantivy::Index;

use crate::schema::build_schema;

#[derive(Serialize, Debug, Default)]
pub struct SyncStats {
    pub documents: u64,
    pub index_dir: String,
}

struct KnowledgeRow {
    id: u64,
    url: String,
    title: String,
    content: String,
    source_name: String,
    source_category: String,
    author: String,
    published_date: String,
    word_count: u64,
    quality_score: f64,
    authority_score: f64,
    freshness_score: f64,
    media_json: String,
}

pub fn sync_from_sqlite(db_path: &str, index_dir: &str) -> Result<SyncStats> {
    let (schema, fields) = build_schema();
    let tmp_dir = format!("{index_dir}.tmp");

    if Path::new(&tmp_dir).exists() {
        fs::remove_dir_all(&tmp_dir)?;
    }
    fs::create_dir_all(&tmp_dir)?;
    let index = Index::create_in_dir(&tmp_dir, schema)?;
    let mut writer = index.writer(256_000_000)?;

    let conn = Connection::open(db_path)?;
    let mut stmt = conn.prepare(
        "SELECT id, url, COALESCE(title,''), COALESCE(content_text,''),
                COALESCE(source_name,''), COALESCE(source_category,''),
                COALESCE(author,''), COALESCE(published_date,''),
                COALESCE(word_count,0), COALESCE(quality_score,0),
                COALESCE(authority_score,0), COALESCE(freshness_score,0),
                COALESCE(media_json,'')
         FROM knowledge_items",
    )?;

    let rows = stmt.query_map([], |row| {
        Ok(KnowledgeRow {
            id: row.get::<_, i64>(0)? as u64,
            url: row.get(1)?,
            title: row.get(2)?,
            content: row.get(3)?,
            source_name: row.get(4)?,
            source_category: row.get(5)?,
            author: row.get(6)?,
            published_date: row.get(7)?,
            word_count: row.get::<_, i64>(8)? as u64,
            quality_score: row.get(9)?,
            authority_score: row.get(10)?,
            freshness_score: row.get(11)?,
            media_json: row.get(12)?,
        })
    })?;

    let mut count = 0u64;
    for row in rows {
        let r = row?;
        writer.add_document(doc!(
            fields.id => r.id,
            fields.url => r.url,
            fields.title => r.title,
            fields.content => r.content,
            fields.source_name => r.source_name,
            fields.source_category => r.source_category,
            fields.author => r.author,
            fields.published_date => r.published_date,
            fields.word_count => r.word_count,
            fields.quality_score => r.quality_score,
            fields.authority_score => r.authority_score,
            fields.freshness_score => r.freshness_score,
            fields.media_json => r.media_json,
        ))?;
        count += 1;
    }

    writer.commit()?;
    writer.wait_merging_threads()?;

    if Path::new(index_dir).exists() {
        fs::remove_dir_all(index_dir)?;
    }
    fs::rename(&tmp_dir, index_dir)?;

    Ok(SyncStats {
        documents: count,
        index_dir: index_dir.to_string(),
    })
}
