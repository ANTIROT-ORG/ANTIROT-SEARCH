//! Search: phrase-first BM25 with OR fallback and the same
//! text/authority/quality/freshness blend used by the Python and TS layers.

use anyhow::Result;
use serde::Serialize;
use std::collections::HashSet;
use tantivy::collector::TopDocs;
use tantivy::query::{BooleanQuery, BoostQuery, Occur, PhraseQuery, Query, TermQuery};
use tantivy::schema::{IndexRecordOption, Value};
use tantivy::{Index, IndexReader, Searcher, TantivyDocument, Term};

use crate::schema::{Fields, TITLE_BOOST};

#[derive(Serialize, Clone, Debug)]
pub struct SearchHit {
    pub id: u64,
    pub url: String,
    pub title: String,
    pub content_snippet: String,
    pub source_name: String,
    pub source_category: String,
    pub author: String,
    pub published_date: String,
    pub word_count: u64,
    pub quality_score: f64,
    pub authority_score: f64,
    pub freshness_score: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub media: Option<serde_json::Value>,
    pub similarity_score: f64,
    pub search_type: String,
}

pub fn tokenize(query: &str) -> Vec<String> {
    query
        .to_lowercase()
        .split(|c: char| !c.is_alphanumeric())
        .filter(|token| token.len() > 1)
        .map(|token| token.to_string())
        .collect()
}

pub fn blend(text: f64, quality: f64, authority: f64, freshness: f64) -> f64 {
    0.50 * text + 0.20 * authority + 0.15 * quality + 0.15 * freshness
}

fn phrase_query(fields: &Fields, tokens: &[String]) -> Box<dyn Query> {
    let title_terms: Vec<Term> = tokens
        .iter()
        .map(|t| Term::from_field_text(fields.title, t))
        .collect();
    let content_terms: Vec<Term> = tokens
        .iter()
        .map(|t| Term::from_field_text(fields.content, t))
        .collect();

    Box::new(BooleanQuery::new(vec![
        (
            Occur::Should,
            Box::new(BoostQuery::new(
                Box::new(PhraseQuery::new(title_terms)),
                TITLE_BOOST,
            )) as Box<dyn Query>,
        ),
        (
            Occur::Should,
            Box::new(PhraseQuery::new(content_terms)) as Box<dyn Query>,
        ),
    ]))
}

fn or_query(fields: &Fields, tokens: &[String]) -> Box<dyn Query> {
    let mut clauses: Vec<(Occur, Box<dyn Query>)> = Vec::new();
    for token in tokens {
        clauses.push((
            Occur::Should,
            Box::new(BoostQuery::new(
                Box::new(TermQuery::new(
                    Term::from_field_text(fields.title, token),
                    IndexRecordOption::WithFreqs,
                )),
                TITLE_BOOST,
            )) as Box<dyn Query>,
        ));
        clauses.push((
            Occur::Should,
            Box::new(TermQuery::new(
                Term::from_field_text(fields.content, token),
                IndexRecordOption::WithFreqs,
            )) as Box<dyn Query>,
        ));
    }
    Box::new(BooleanQuery::new(clauses))
}

fn run_query(
    searcher: &Searcher,
    query: &dyn Query,
    limit: usize,
    fields: &Fields,
    tokens: &[String],
) -> Result<Vec<(SearchHit, f64)>> {
    let top_docs = searcher.search(query, &TopDocs::with_limit(limit))?;
    let mut out = Vec::with_capacity(top_docs.len());

    for (score, address) in top_docs {
        let doc: TantivyDocument = searcher.doc(address)?;
        let text = |field| -> String {
            doc.get_first(field)
                .and_then(|value| value.as_str())
                .unwrap_or("")
                .to_string()
        };
        let number = |field| -> f64 {
            doc.get_first(field)
                .and_then(|value| value.as_f64())
                .unwrap_or(0.0)
        };
        let integer = |field| -> u64 {
            doc.get_first(field)
                .and_then(|value| value.as_u64())
                .unwrap_or(0)
        };

        let content = text(fields.content);
        let media_json = text(fields.media_json);
        let media = serde_json::from_str(&media_json).ok();

        out.push((
            SearchHit {
                id: integer(fields.id),
                url: text(fields.url),
                title: text(fields.title),
                content_snippet: make_snippet(&content, tokens),
                source_name: text(fields.source_name),
                source_category: text(fields.source_category),
                author: text(fields.author),
                published_date: text(fields.published_date),
                word_count: integer(fields.word_count),
                quality_score: number(fields.quality_score),
                authority_score: number(fields.authority_score),
                freshness_score: number(fields.freshness_score),
                media,
                similarity_score: 0.0,
                search_type: "tantivy".to_string(),
            },
            score as f64,
        ));
    }

    Ok(out)
}

pub fn make_snippet(content: &str, tokens: &[String]) -> String {
    if content.is_empty() {
        return String::new();
    }

    let lower = content.to_lowercase();
    let position = tokens.iter().find_map(|token| lower.find(token.as_str()));

    let window_start = match position {
        Some(p) => p.saturating_sub(60),
        None => 0,
    };

    let mut start = 0usize;
    for (index, _) in content.char_indices() {
        if index <= window_start {
            start = index;
        } else {
            break;
        }
    }

    let mut end = (start + 320).min(content.len());
    while end < content.len() && !content.is_char_boundary(end) {
        end -= 1;
    }

    let mut snippet = content[start..end].trim().to_string();
    if end < content.len() {
        snippet.push_str("...");
    }
    snippet
}

pub fn search_index(
    index: &Index,
    reader: &IndexReader,
    fields: &Fields,
    query: &str,
    limit: usize,
    category: Option<&str>,
) -> Result<Vec<SearchHit>> {
    let tokens = tokenize(query);
    if tokens.is_empty() {
        return Ok(Vec::new());
    }

    let searcher = reader.searcher();
    let fetch = (limit * 5).max(20);

    let mut merged: Vec<(SearchHit, f64)> = Vec::new();

    if tokens.len() > 1 {
        merged = run_query(&searcher, phrase_query(fields, &tokens).as_ref(), fetch, fields, &tokens)?;
    }

    // OR fallback / expansion when phrase recall is low
    if merged.len() < 5 {
        let extra = run_query(&searcher, or_query(fields, &tokens).as_ref(), fetch, fields, &tokens)?;
        let seen: HashSet<u64> = merged.iter().map(|(hit, _)| hit.id).collect();
        for (hit, raw) in extra {
            if !seen.contains(&hit.id) {
                merged.push((hit, raw * 0.5)); // expanded matches rank below direct ones
            }
        }
    }

    if let Some(category) = category {
        merged.retain(|(hit, _)| hit.source_category == category);
    }

    let max_raw = merged
        .iter()
        .map(|(_, raw)| *raw)
        .fold(0.0f64, f64::max)
        .max(1e-9);

    let mut hits: Vec<SearchHit> = merged
        .into_iter()
        .map(|(mut hit, raw)| {
            let text_score = (raw / max_raw).min(1.0);
            hit.similarity_score = blend(
                text_score,
                hit.quality_score,
                hit.authority_score,
                hit.freshness_score,
            );
            hit
        })
        .collect();

    hits.sort_by(|a, b| {
        b.similarity_score
            .partial_cmp(&a.similarity_score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    hits.truncate(limit);

    // `index` is kept in the signature for future per-index state (e.g. sharding)
    let _ = index;

    Ok(hits)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::build_schema;
    use tantivy::doc;
    use tantivy::Index;

    #[test]
    fn tokenize_splits_hyphens_and_case() {
        assert_eq!(tokenize("Present-Day Climate"), vec!["present", "day", "climate"]);
        assert!(tokenize("a b").is_empty());
    }

    #[test]
    fn blend_weights_text_dominantly() {
        assert!(blend(1.0, 0.5, 0.5, 0.5) > blend(0.2, 0.5, 0.5, 0.5));
        assert!(blend(0.0, 0.0, 0.0, 0.0).abs() < f64::EPSILON);
    }

    #[test]
    fn search_prefers_phrase_and_title() {
        let (schema, fields) = build_schema();
        let dir = tempfile::tempdir().unwrap();
        let index = Index::create_in_dir(dir.path(), schema).unwrap();
        let mut writer = index.writer(15_000_000).unwrap();

        writer
            .add_document(doc!(
                fields.id => 1u64,
                fields.url => "https://example.com/machine-learning",
                fields.title => "Machine learning",
                fields.content => "Machine learning is a field of study in artificial intelligence.",
                fields.source_name => "Wikipedia",
                fields.source_category => "science_research",
                fields.author => "",
                fields.published_date => "",
                fields.word_count => 10u64,
                fields.quality_score => 0.9f64,
                fields.authority_score => 0.9f64,
                fields.freshness_score => 0.5f64,
                fields.media_json => "",
            ))
            .unwrap();
        writer
            .add_document(doc!(
                fields.id => 2u64,
                fields.url => "https://example.com/neural",
                fields.title => "Neural network (machine learning)",
                fields.content => "Networks learn from data. Learning algorithms adjust weights.",
                fields.source_name => "Wikipedia",
                fields.source_category => "science_research",
                fields.author => "",
                fields.published_date => "",
                fields.word_count => 10u64,
                fields.quality_score => 0.6f64,
                fields.authority_score => 0.6f64,
                fields.freshness_score => 0.5f64,
                fields.media_json => "",
            ))
            .unwrap();
        writer.commit().unwrap();
        writer.wait_merging_threads().unwrap();

        let reader = index.reader().unwrap();
        let hits = search_index(&index, &reader, &fields, "machine learning", 5, None).unwrap();

        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].url, "https://example.com/machine-learning");
        assert!(hits[0].similarity_score > hits[1].similarity_score);
    }

    #[test]
    fn make_snippet_centers_on_query_term() {
        let content = format!(
            "{} chlorophyll absorbs light {}",
            "x ".repeat(80),
            "y ".repeat(80)
        );
        let snippet = make_snippet(&content, &["chlorophyll".to_string()]);
        assert!(snippet.contains("chlorophyll"));
        assert!(snippet.len() <= 323);
    }
}
