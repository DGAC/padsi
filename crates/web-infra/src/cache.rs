//
// Copyright (c) 2025-2026 DGAC/DSNA
//
// This file is part of PADSI.
//
// This software is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This software is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this software.  If not, see <http://www.gnu.org/licenses/>.
//

//!
//! Certificates cache
//!

use padsi::pki::PKCS12;
use std::borrow::Borrow;
use std::collections::{HashMap, VecDeque};
use std::hash::Hash;

#[derive(Debug)]
pub struct SizedCache<K, V> {
    cache: HashMap<K, V>,
    queue: VecDeque<K>,
    max_size: usize,
}

impl<K: Eq + Hash + Clone, V> SizedCache<K, V> {
    pub fn new(max_size: usize) -> Self {
        SizedCache {
            cache: HashMap::new(),
            queue: VecDeque::new(),
            max_size,
        }
    }

    pub fn add(&mut self, key: K, value: V) {
        // if key already exists, just the hash
        if self.cache.contains_key(&key) {
            self.cache.insert(key, value);
            return;
        }
        // get rid of the last inserted item if full
        if self.cache.len() == self.max_size {
            if let Some(old_key) = self.queue.pop_front() {
                self.cache.remove(&old_key);
            }
        }

        // insert the new item
        self.queue.push_back(key.clone());
        self.cache.insert(key, value);
    }

    pub fn get<Q>(&self, key: &Q) -> Option<&V>
    where
        Q: ?Sized,
        K: Borrow<Q>,
        Q: Hash + Eq,
        // Bounds from impl:
        K: Eq + Hash,
    {
        self.cache.get(key)
    }
}

///
/// Certificate cache based on the CN in the certificate
///
#[derive(Debug)]
pub struct CertificatesCache(SizedCache<String, PKCS12>);

impl CertificatesCache {
    /// Create a new cache
    pub fn new(max_size: usize) -> Self {
        Self(SizedCache::new(max_size))
    }

    /// Add a certificate to the cache
    pub fn add(&mut self, p12: PKCS12) {
        let attrs = p12.cert().attributes();
        self.0.add(attrs.cn, p12);
    }

    /// Get a reference to a cached certificate
    pub fn get(&self, cn: &str) -> Option<&PKCS12> {
        self.0.get(cn)
    }
}
