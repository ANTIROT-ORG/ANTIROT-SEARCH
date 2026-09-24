import { describe, it, expect } from 'vitest';
import {
  isDictionaryQuery, mapWiktionary, extractIpa, extractAudioFile, audioFileUrl,
} from './dictionary';
import { audioHostAllowed } from '../pages/api/dict/audio';

describe('isDictionaryQuery', () => {
  it('accepts single words regardless of case', () => {
    expect(isDictionaryQuery('human', false, 'all', 1)).toBe(true);
    expect(isDictionaryQuery('Human', false, 'all', 1)).toBe(true);
    expect(isDictionaryQuery("don't", false, 'all', 1)).toBe(true);
    expect(isDictionaryQuery('ice cream', false, 'all', 1)).toBe(true);
  });

  it('rejects entity-style capitalized two-word queries', () => {
    expect(isDictionaryQuery('Alan Turing', false, 'all', 1)).toBe(false);
    expect(isDictionaryQuery('New York', false, 'all', 1)).toBe(false);
  });

  it('rejects operators, other verticals and deep pages', () => {
    expect(isDictionaryQuery('human', true, 'all', 1)).toBe(false);
    expect(isDictionaryQuery('human', false, 'images', 1)).toBe(false);
    expect(isDictionaryQuery('human', false, 'all', 2)).toBe(false);
  });

  it('rejects URLs, numbers, overlong and empty queries', () => {
    expect(isDictionaryQuery('example.com/x', false, 'all', 1)).toBe(false);
    expect(isDictionaryQuery('12345', false, 'all', 1)).toBe(false);
    expect(isDictionaryQuery('a very long phrase beyond limits', false, 'all', 1)).toBe(false);
    expect(isDictionaryQuery('a', false, 'all', 1)).toBe(false);
    expect(isDictionaryQuery('', false, 'all', 1)).toBe(false);
  });
});

describe('mapWiktionary', () => {
  const payload = {
    en: [
      {
        partOfSpeech: 'Adjective',
        definitions: [
          { definition: '' },  // empty senses are skipped
          { definition: 'Of or belonging to the species <a href="/x">Homo sapiens</a>.' },
        ],
      },
      {
        partOfSpeech: 'Noun',
        definitions: [
          { definition: 'A human being.', examples: ['All humans are mortal.'] },
        ],
      },
    ],
  };

  it('maps POS blocks, strips HTML and skips empty definitions', () => {
    const entry = mapWiktionary(payload, 'human');
    expect(entry).toBeTruthy();
    expect(entry!.found).toBe(true);
    expect(entry!.source).toBe('wiktionary');
    expect(entry!.phonetic).toBe('');
    expect(entry!.definitions).toHaveLength(2);
    expect(entry!.definitions[0].pos).toBe('adjective');
    expect(entry!.definitions[0].text).toContain('Homo sapiens');
    expect(entry!.definitions[0].text).not.toContain('<a');
    expect(entry!.definitions[1].example).toBe('All humans are mortal.');
  });

  it('returns null for missing or empty payloads', () => {
    expect(mapWiktionary(undefined, 'zzz')).toBeNull();
    expect(mapWiktionary({}, 'zzz')).toBeNull();
    expect(mapWiktionary({ en: [] }, 'zzz')).toBeNull();
    expect(mapWiktionary({ en: [{ partOfSpeech: 'X', definitions: [{ definition: '' }] }] }, 'x')).toBeNull();
  });
});

describe('pronunciation extraction', () => {
  const wikitext = `
==English==
===Pronunciation===
* {{IPA|en|/ˈhjuː.mən/|[ˈçju̟mən]}}
* {{IPA|de|/huˈmaːn/}}
====Noun====
# a person
{{audio|en|en-us-human.ogg|a=US}}
{{audio|en|De-human.ogg|a=DE}}
`;

  it('extracts the first English IPA (ignores other languages)', () => {
    expect(extractIpa(wikitext)).toBe('/ˈhjuː.mən/');
    expect(extractIpa('no pronunciations here')).toBe('');
  });

  it('extracts the first English audio filename', () => {
    expect(extractAudioFile(wikitext)).toBe('en-us-human.ogg');
    expect(extractAudioFile('nothing')).toBe('');
  });

  it('builds a Commons redirect URL', () => {
    expect(audioFileUrl('En-us-human.ogg')).toBe(
      'https://commons.wikimedia.org/wiki/Special:Redirect/file/En-us-human.ogg'
    );
  });
});

describe('audio proxy host allowlist', () => {
  it('accepts allowlisted https hosts and subdomains', () => {
    expect(audioHostAllowed('https://upload.wikimedia.org/w/x.ogg')).toBe(true);
    expect(audioHostAllowed('https://commons.wikimedia.org/wiki/Special:Redirect/file/x.ogg')).toBe(true);
    expect(audioHostAllowed('https://api.dictionaryapi.com/media/x.mp3')).toBe(true);
  });

  it('rejects everything else', () => {
    expect(audioHostAllowed('http://upload.wikimedia.org/x.ogg')).toBe(false);
    expect(audioHostAllowed('https://evil.com/x.mp3')).toBe(false);
    expect(audioHostAllowed('https://upload.wikimedia.org.evil.com/x')).toBe(false);
    expect(audioHostAllowed('https://user:pass@upload.wikimedia.org/x')).toBe(false);
    expect(audioHostAllowed('https://evildictionaryapi.com/x')).toBe(false);
    expect(audioHostAllowed('file:///etc/passwd')).toBe(false);
    expect(audioHostAllowed('not a url')).toBe(false);
    expect(audioHostAllowed('')).toBe(false);
  });
});
