# Examples

* `demo_offline.vcf`: synthetic variants (fake genes SYNG1..9 on chr1) that only exist in the offline fixtures.
  Use it to try batch mode without keys: `aegis batch examples/demo_offline.vcf --mode offline`.
  It also contains a multi-allelic site, a symbolic `<DEL>` allele, a LowQual record and a chrM record
  so you can see how each is handled.
* For live mode, use your own GRCh38 VCF (or a CSV with a `variant` column, e.g. `17-43045712-T-C`).
