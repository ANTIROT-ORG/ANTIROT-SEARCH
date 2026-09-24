import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
import type { ProviderResult } from './providers/types';
import { cached } from './providers/cache';
import { getTurso } from './turso';

const execFileAsync = promisify(execFile);
const PYTHON_BIN = process.env.PYTHON_BIN || 'python3';
const REPO_ROOT = path.resolve(process.cwd(), '..');

/**
 * Fast-Tier Client (Tier 1: DuckDB / MotherDuck / SQLite Fast-Rank)
 * Ultra-low compute: queries in-process (<1ms) without spawning Python processes,
 * falling back to MotherDuck cloud when configured.
 */
export async function queryFastTier(q: string, limit = 10, category?: string): Promise<ProviderResult[]> {
  if (!q.trim()) return [];

  const cacheKey = `fast_tier:${q.toLowerCase()}:${limit}:${category || 'all'}`;
  const res = await cached(cacheKey, 600, async () => {
    // 1. If MotherDuck cloud token is configured, use cloud analytics tier
    if (process.env.MOTHERDUCK_TOKEN) {
      return queryMotherDuckCloud(q, limit, category);
    }

    // 2. High-performance In-Process Tier (<1ms, 0% CPU fork/exec overhead)
    try {
      const db = getTurso();
      const tokens = q.trim().toLowerCase().split(/\s+/).filter(t => t.length > 1);

      let sql = '';
      const args: any[] = [];

      if (!tokens.length) {
        let catClause = '';
        if (category) {
          catClause = 'WHERE source_category = ?';
          args.push(category);
        }
        sql = `
          SELECT id, url, title, source_name, source_category,
                 SUBSTR(content_text, 1, 320) AS snippet, author, published_date,
                 quality_score, overall_rank, media_json
          FROM knowledge_items
          ${catClause}
          ORDER BY overall_rank DESC
          LIMIT ?
        `;
        args.push(limit);
      } else {
        const matchClauses: string[] = [];
        for (const t of tokens) {
          matchClauses.push('(LOWER(title) LIKE ? OR LOWER(content_text) LIKE ?)');
          args.push(`%${t}%`, `%${t}%`);
        }
        let catClause = '';
        if (category) {
          catClause = ' AND source_category = ?';
          args.push(category);
        }
        sql = `
          SELECT id, url, title, source_name, source_category,
                 SUBSTR(content_text, 1, 320) AS snippet, author, published_date,
                 quality_score, overall_rank, media_json,
                 ((CASE WHEN LOWER(title) LIKE ? THEN 2.0 ELSE 1.0 END) * overall_rank) AS score
          FROM knowledge_items
          WHERE (${matchClauses.join(' AND ')})${catClause}
          ORDER BY score DESC, overall_rank DESC
          LIMIT ?
        `;
        args.unshift(`%${tokens[0]}%`); // For the CASE WHEN
        args.push(limit);
      }

      const res = await db.execute({ sql, args });
      if (res.rows && res.rows.length) {
        return res.rows.map((r: any) => {
          let media = [];
          if (r.media_json) {
            try { media = JSON.parse(r.media_json); } catch {}
          }
          return {
            id: `fast-${r.id}`,
            title: String(r.title || ''),
            url: String(r.url || ''),
            snippet: String(r.snippet || ''),
            provider: 'duckdb',
            result_type: 'web' as const,
            source_name: String(r.source_name || ''),
            published_date: String(r.published_date || ''),
            quality_score: Number(r.quality_score) || 0,
            score: Number(r.overall_rank) || 0,
            media,
          };
        });
      }
    } catch {
      // In-process query error, gracefully fall back
    }

    return queryMotherDuckCloud(q, limit, category);
  });
  return res || [];
}

async function queryMotherDuckCloud(q: string, limit: number, category?: string): Promise<ProviderResult[]> {
  try {
    const script = `
import json, sys
from search.duckdb_tier import DuckDBFastTier
tier = DuckDBFastTier()
res = tier.fast_search(sys.argv[1], limit=int(sys.argv[2]), category=sys.argv[3] if sys.argv[3] else None)
print(json.dumps(res))
`;
    const { stdout } = await execFileAsync(PYTHON_BIN, [
      '-c', script, q, String(limit), category || ''
    ], {
      cwd: REPO_ROOT,
      timeout: 2500,
    });

    const parsed = JSON.parse(stdout || '[]');
    return parsed.map((item: any) => ({
      id: `fast-${item.id}`,
      title: item.title,
      url: item.url,
      snippet: item.snippet,
      provider: item.tier || 'duckdb',
      result_type: 'web' as const,
      source_name: item.source_name,
      published_date: item.published_date,
      quality_score: item.quality_score,
      score: item.overall_rank,
      media: item.media,
    }));
  } catch {
    return [];
  }
}
