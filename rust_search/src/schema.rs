//! Tantivy schema + index helpers for the RatSearch serving index.

use tantivy::schema::{
    Field, IndexRecordOption, NumericOptions, Schema, TextFieldIndexing, TextOptions,
};
pub const TITLE_BOOST: f32 = 3.0;

#[derive(Clone)]
pub struct Fields {
    pub id: Field,
    pub url: Field,
    pub title: Field,
    pub content: Field,
    pub source_name: Field,
    pub source_category: Field,
    pub author: Field,
    pub published_date: Field,
    pub word_count: Field,
    pub quality_score: Field,
    pub authority_score: Field,
    pub freshness_score: Field,
    pub media_json: Field,
}

pub fn build_schema() -> (Schema, Fields) {
    let mut builder = Schema::builder();

    let id = builder.add_u64_field("id", NumericOptions::default().set_stored().set_fast());
    let url = builder.add_text_field("url", TextOptions::default().set_stored());

    let indexed_text = TextOptions::default()
        .set_stored()
        .set_indexing_options(
            TextFieldIndexing::default()
                .set_tokenizer("default")
                .set_index_option(IndexRecordOption::WithFreqsAndPositions),
        );

    let title = builder.add_text_field("title", indexed_text.clone());
    let content = builder.add_text_field("content", indexed_text);
    let source_name = builder.add_text_field("source_name", TextOptions::default().set_stored());
    let source_category =
        builder.add_text_field("source_category", TextOptions::default().set_stored());
    let author = builder.add_text_field("author", TextOptions::default().set_stored());
    let published_date =
        builder.add_text_field("published_date", TextOptions::default().set_stored());

    let word_count = builder.add_u64_field("word_count", NumericOptions::default().set_stored());
    let quality_score =
        builder.add_f64_field("quality_score", NumericOptions::default().set_stored());
    let authority_score =
        builder.add_f64_field("authority_score", NumericOptions::default().set_stored());
    let freshness_score =
        builder.add_f64_field("freshness_score", NumericOptions::default().set_stored());
    let media_json = builder.add_text_field("media_json", TextOptions::default().set_stored());

    let schema = builder.build();
    let fields = Fields {
        id,
        url,
        title,
        content,
        source_name,
        source_category,
        author,
        published_date,
        word_count,
        quality_score,
        authority_score,
        freshness_score,
        media_json,
    };
    (schema, fields)
}
