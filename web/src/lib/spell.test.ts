import { describe, it, expect } from 'vitest';
import { didYouMean, exactCorrection, editDistance } from './spell';

describe('editDistance', () => {
  it('computes Levenshtein distance', () => {
    expect(editDistance('kitten', 'sitting')).toBe(3);
    expect(editDistance('mechanics', 'mechanics')).toBe(0);
    expect(editDistance('abc', '')).toBe(3);
  });
});

describe('exactCorrection', () => {
  it('corrects whole-query hits', () => {
    expect(exactCorrection('quantum mechancis')).toBe('quantum mechanics');
    expect(exactCorrection('Quantum Mechancis')).toBe('quantum mechanics');
  });

  it('corrects adjacent-word (bigram) hits in place', () => {
    expect(exactCorrection('what is machine lerning')).toBe('what is machine learning');
    expect(exactCorrection('artifical intelligence history')).toBe('artificial intelligence history');
  });

  it('returns null for correct or unknown queries', () => {
    expect(exactCorrection('machine learning')).toBeNull();
    expect(exactCorrection('completely unrelated text')).toBeNull();
    expect(exactCorrection('')).toBeNull();
  });
});

describe('didYouMean', () => {
  it('applies curated exact corrections', () => {
    expect(didYouMean('reciepe')).toBe('recipe');
    expect(didYouMean('enviroment')).toBe('environment');
  });

  it('falls back to fuzzy matching against the correction vocabulary', () => {
    // 'vitamines' is not a curated key but is within distance 2 of 'vitamin'
    expect(didYouMean('vitamines')).toBe('vitamin');
  });

  it('leaves correct queries alone', () => {
    expect(didYouMean('periodic table')).toBeNull();
    expect(didYouMean('photosynthesis')).toBeNull();
  });
});
