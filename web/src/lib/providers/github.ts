import { fetchJson, stripHtml, truncate, env } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * GitHub code + repository search.
 * Code search requires GITHUB_TOKEN (10 req/min authenticated).
 * Without a token, only the local docs index backs the Code tab.
 */

interface GitHubCodeResponse {
  items?: {
    name?: string;
    path?: string;
    sha?: string;
    html_url?: string;
    repository?: {
      full_name?: string;
      html_url?: string;
      description?: string;
      stargazers_count?: number;
      language?: string;
      owner?: { login?: string };
    };
    text_matches?: { fragment?: string }[];
  }[];
}

interface GitHubRepoResponse {
  items?: {
    full_name?: string;
    html_url?: string;
    description?: string;
    stargazers_count?: number;
    language?: string;
    owner?: { login?: string };
    updated_at?: string;
  }[];
}

export function githubConfigured(): boolean {
  return !!env('GITHUB_TOKEN');
}

function githubHeaders(): Record<string, string> {
  return {
    Authorization: `Bearer ${env('GITHUB_TOKEN')}`,
    Accept: 'application/vnd.github.text-match+json',
    'X-GitHub-Api-Version': '2022-11-28',
  };
}

export async function searchCode(q: string, limit = 20, page = 1): Promise<ProviderResult[]> {
  const token = env('GITHUB_TOKEN');
  if (!token || !q.trim()) return [];

  const url = 'https://api.github.com/search/code' +
    `?q=${encodeURIComponent(q)}&per_page=${Math.min(limit, 30)}&page=${page}`;

  const data = await cached(`gh:code:${q.toLowerCase()}:${limit}:${page}`, 600, () =>
    fetchJson<GitHubCodeResponse>(url, { headers: githubHeaders(), timeoutMs: 8000 })
  );

  const items = data?.items;
  if (!items || !items.length) return searchRepos(q, limit, page);

  return items
    .filter(it => it.html_url && it.repository?.full_name)
    .map(it => ({
      id: `github-${it.sha || it.path}`,
      title: `${it.repository!.full_name}: ${it.path || it.name || ''}`,
      url: it.html_url!,
      snippet: truncate(stripHtml(it.text_matches?.[0]?.fragment || ''), 300),
      provider: 'github',
      result_type: 'code' as const,
      source_name: 'GitHub',
      repo: it.repository!.full_name,
      path: it.path || '',
      language: it.repository!.language || '',
      stars: it.repository!.stargazers_count || 0,
      author: it.repository!.owner?.login || '',
    }));
}

export async function searchRepos(q: string, limit = 20, page = 1): Promise<ProviderResult[]> {
  const token = env('GITHUB_TOKEN');
  if (!token || !q.trim()) return [];

  const url = 'https://api.github.com/search/repositories' +
    `?q=${encodeURIComponent(q)}&per_page=${Math.min(limit, 30)}&page=${page}`;

  const data = await cached(`gh:repos:${q.toLowerCase()}:${limit}:${page}`, 900, () =>
    fetchJson<GitHubRepoResponse>(url, {
      headers: { ...githubHeaders(), Accept: 'application/vnd.github+json' },
      timeoutMs: 8000,
    })
  );

  return (data?.items || [])
    .filter(it => it.html_url && it.full_name)
    .map(it => ({
      id: `github-repo-${it.full_name}`,
      title: it.full_name!,
      url: it.html_url!,
      snippet: truncate(stripHtml(it.description || ''), 240),
      provider: 'github',
      result_type: 'code' as const,
      source_name: 'GitHub',
      repo: it.full_name!,
      language: it.language || '',
      stars: it.stargazers_count || 0,
      author: it.owner?.login || '',
      published_date: it.updated_at || '',
    }));
}
