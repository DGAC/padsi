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
//! Possible usages of certificates, which can be used to build certificate templates
//!
use anyhow::Result;
use rcgen::CertificateParams;

mod tls_server;

pub use tls_server::TlsServer;

///
/// Defines the attributes a certificate will have for a specific usage (like KU and EKU).
///
pub trait CertificateUsage {
    fn name(&self) -> &'static str;
    fn get_params(
        &self,
        builder: &mut CertificateParams,
        cn: &str,
        other_names: Option<impl Into<Vec<String>>>,
    ) -> Result<()>;
}
