from count import Model, _fetch_reads, _find_mapped_count, _fetch_reads_excluding
from eta import ETA
from ngsutils.gtf import GTF
from ngsutils.bed import BedFile
import ngsutils.support.ngs_utils
import os
import sys


class GTFModel(Model):
    def __init__(self, fname):
        self.fname = fname
        Model.__init__(self)

    def get_source(self):
        return self.fname

    def get_name(self):
        return 'gtf'

    def get_headers(self):
        return 'gene geneid isoid chrom strand txstart txend'.split()

    def get_regions(self):
        gtf = GTF(self.fname)
        eta = ETA(gtf.fsize(), fileobj=gtf)

        for gene in gtf.genes:
            eta.print_status(extra=gene.gene_name)
            starts = []
            ends = []

            # just include all regions - don't worry about transcripts and exons
            # the regions encompass all exons anyway...
            for num, start, end, const, names in gene.regions:
                starts.append(start)
                ends.append(end)

            yield (gene.chrom, starts, ends, gene.strand, [gene.gene_name, gene.gene_id, gene.isoform_id, gene.chrom, gene.strand, gene.start, gene.end], None)
        eta.done()


class ExonModel(Model):
    def __init__(self, fname):
        self.fname = fname
        self.stranded = None
        Model.__init__(self)

    def get_source(self):
        return self.fname

    def get_name(self):
        return 'exon'

    def get_headers(self):
        return 'gene geneid isoid chrom strand txstart txend '.split()

    def get_postheaders(self):
        return 'regionstart regionend const_count region_num const_alt count excl_count incl_pct excl_pct alt-index'.split()

    def get_regions(self):
        gtf = GTF(self.fname)
        eta = ETA(gtf.fsize(), fileobj=gtf)

        for gene in gtf.genes:
            eta.print_status(extra=gene.gene_name)
            starts = []
            ends = []
            const_spans = []

            was_last_const = False
            for num, start, end, const, names in gene.regions:
                starts.append(start)
                ends.append(end)

                # assemble a list of lists with contiguous spans of constant regions
                # this will let us count junction-spanning reads that are cover two
                # constant exons/regions

                if const:
                    if not was_last_const:
                        const_spans.append([])
                    const_spans[-1].append((start, end))
                    was_last_const = True
                else:
                    was_last_const = False

            def callback(bam, common_count, common_reads, common_cols):
                # gather constant reads
                const_count = 0
                for span in const_spans:
                    starts = []
                    ends = []

                    for start, end in span:
                        starts.append(start)
                        ends.append(end)

                    count, reads = _fetch_reads(bam, gene.chrom, gene.strand if self.stranded else None, starts, ends, self.multiple, False, self.whitelist, self.blacklist, self.uniq_only, self.rev_read2)
                    const_count += count

                #find counts for each region
                for num, start, end, const, names in gene.regions:
                    count, reads = _fetch_reads(bam, gene.chrom, gene.strand if self.stranded else None, [start], [end], self.multiple, False, self.whitelist, self.blacklist, self.uniq_only, self.rev_read2)
                    excl_count, excl_reads = _fetch_reads_excluding(bam, gene.chrom, gene.strand if self.stranded else None, start, end, self.multiple, self.whitelist, self.blacklist, self.rev_read2)

                    # remove reads that exclude this region
                    for read in excl_reads:
                        if read in reads:
                            reads.remove(read)
                            count = count - 1

                    # find reads that *arent'* in this region
                    other_reads = 0
                    for read in common_reads:
                        if not read in reads and not read in excl_reads:
                            other_reads += 1

                    if other_reads > 0:
                        altindex = float(count - excl_count) / other_reads
                    else:
                        altindex = ''

                    if len(common_reads) > 0:
                        incl_pct = float(count) / len(common_reads)
                        excl_pct = float(excl_count) / len(common_reads)
                    else:
                        incl_pct = ''
                        excl_pct = ''

                    cols = common_cols[:]
                    cols.append(start)
                    cols.append(end)
                    cols.append(const_count)
                    cols.append(num)
                    cols.append('const' if const else 'alt')
                    cols.append(count)
                    cols.append(excl_count)
                    cols.append(incl_pct)
                    cols.append(excl_pct)
                    cols.append(altindex)
                    yield cols

            yield (gene.chrom, starts, ends, gene.strand, [gene.gene_name, gene.gene_id, gene.isoform_id, gene.chrom, gene.strand, gene.start, gene.end], callback)
        eta.done()

    def count(self, bam, stranded=False, coverage=False, uniq_only=False, fpkm=False, norm='', multiple='complete', whitelist=None, blacklist=None, out=sys.stdout, quiet=False, rev_read2=False, start_only=False):
        self.stranded = stranded
        self.uniq_only = uniq_only
        self.multiple = multiple
        self.whitelist = whitelist
        self.blacklist = blacklist
        self.rev_read2 = rev_read2

        Model.count(self, bam, stranded, coverage, uniq_only, fpkm, norm, multiple, whitelist, blacklist, out, quiet, rev_read2, start_only)


class BinModel(Model):
    def __init__(self, binsize):
        self.binsize = int(binsize)
        Model.__init__(self)

    def get_source(self):
        return str(self.binsize)

    def get_name(self):
        return 'bin'

    def get_headers(self):
        return 'chrom start end strand'.split()

    def get_regions(self):
        total = 0
        for chrom, chrom_len in self.chrom_lens:
            total += (chrom_len / self.binsize)
            if chrom_len % self.binsize != 0:
                total += 1

        eta = ETA(total)
        pos_acc = 0
        for chrom, chrom_len in self.chrom_lens:
            pos = -1
            for bin in xrange(0, chrom_len, self.binsize):
                if pos > -1:
                    eta.print_status(pos_acc, extra='%s:%s[+]' % (chrom, bin))
                    yield (chrom, [pos], [bin], '+', [chrom, pos, bin, '+'], None)
                    if self.stranded:
                        eta.print_status(pos_acc, extra='%s:%s[-]' % (chrom, bin))
                        yield (chrom, [pos], [bin], '-', [chrom, pos, bin, '-'], None)
                pos = bin
                pos_acc += 1

            eta.print_status(pos_acc, extra='%s:%s[+]' % (chrom, bin))
            yield (chrom, [pos], [chrom_len], '+', [chrom, pos, chrom_len, '+'], None)
            if self.stranded:
                eta.print_status(pos_acc, extra='%s:%s[-]' % (chrom, bin))
                yield (chrom, [pos], [chrom_len], '- ', [chrom, pos, chrom_len, '-'], None)

        eta.done()

    def count(self, bam, stranded=False, coverage=False, uniq_only=False, fpkm=False, norm='', multiple='complete', whitelist=None, blacklist=None, out=sys.stdout, quiet=False, rev_read2=False, start_only=False):
        self.stranded = stranded
        self.chrom_lens = []

        for chrom, chrom_len in zip(bam.references, bam.lengths):
            self.chrom_lens.append((chrom, chrom_len))
        Model.count(self, bam, stranded, coverage, uniq_only, fpkm, norm, multiple, whitelist, blacklist, out, quiet, rev_read2, start_only)


class BEDModel(Model):
    def __init__(self, fname=None, fileobj=None):
        if fileobj:
            self.bed = BedFile(fileobj=fileobj)
            self.fname = '*fileobj*'
        else:
            self.bed = BedFile(fname)
            self.fname = fname
        Model.__init__(self)

    def get_source(self):
        return self.fname

    def get_name(self):
        return 'bed'

    def get_headers(self):
        return 'chrom start end name score strand'.split()

    def get_regions(self):
        eta = ETA(self.bed.length, fileobj=self.bed)
        for region in self.bed:
            eta.print_status(extra='%s:%s-%s[%s]' % (region.chrom, region.start, region.end, region.strand))
            yield (region.chrom, [region.start], [region.end], region.strand, [region.chrom, region.start, region.end, region.name, region.score, region.strand], None)
        eta.done()


def _repeatreader(fname):
    with ngsutils.support.ngs_utils.gzip_opener(fname) as repeat_f:
        eta = ETA(os.stat(fname).st_size, fileobj=repeat_f)
        repeat_f.next()
        repeat_f.next()
        repeat_f.next()

        for line in repeat_f:
            cols = line.strip().split()
            chrom = cols[4]
            start = int(cols[5]) - 1
            end = int(cols[6])
            strand = '+' if cols[8] == '+' else '-'
            family = cols[10]
            member = cols[9]

            eta.print_status(extra='%s|%s %s:%s-%s[%s]' % (family, member, chrom, start, end, strand))
            yield (family, member, chrom, start, end, strand)
        eta.done()


class RepeatModel(Model):
    def __init__(self, fname):
        self.fname = fname
        Model.__init__(self)

    def get_source(self):
        return self.fname

    def get_name(self):
        return 'repeat'

    def get_headers(self):
        return 'family repeat chrom start end strand'.split()

    def get_regions(self):
        for family, member, chrom, start, end, strand in _repeatreader(self.fname):
            yield (chrom, [start], [end], strand, [family, member, chrom, start, end, strand], None)


class RepeatFamilyModel(Model):
    def __init__(self, fname):
        self.fname = fname
        Model.__init__(self)

    def get_source(self):
        return self.fname

    def get_name(self):
        return 'repeatfam'

    def get_headers(self):
        return 'family repeat'.split()

    def get_regions(self):
        for family, member, chrom, start, end, strand in _repeatreader(self.fname):
            yield (chrom, [start], [end], strand, [family, member, chrom, start, end, strand], None)

    def count(self, bam, stranded=False, coverage=False, uniq_only=False, fpkm=False, norm='', multiple='complete', whitelist=None, blacklist=None, out=sys.stdout, quiet=False, rev_read2=False, start_only=False):
        # This is a separate count implementation because for repeat families,
        # we need to combine the counts from multiple regions in the genome,
        # so the usual chrom, starts, ends loop breaks down.

        if coverage:
            sys.stderr.write('Coverage calculations not supported with repeatmasker family models\n')
            sys.exit(1)

        if norm and norm not in ['all', 'mapped']:
            sys.stderr.write('Normalization "%s" not supported with repeatmasker family models\n' % norm)
            sys.exit(1)

        # multireads = set()
        # single_count = 0
        repeats = {}
        total_count = 0.0
        for family, member, chrom, start, end, strand in _repeatreader(self.fname):
            if not (family, member) in repeats:
                repeats[(family, member)] = {'count': 0, 'size': 0}

            if not (family, '*') in repeats:
                repeats[(family, '*')] = {'count': 0, 'size': 0}

            if not chrom in bam.references:
                continue

            size = end - start
            repeats[(family, '*')]['size'] += size
            repeats[(family, member)]['size'] += size

            count, reads = _fetch_reads(bam, chrom, strand if stranded else None, [start], [end], multiple, False, whitelist, blacklist, rev_read2)
            repeats[(family, '*')]['count'] += count
            repeats[(family, member)]['count'] += count

            total_count += count

            # for read in reads:
            #     if read.tags and 'IH' in read.tags:
            #         ih = int(read.opt('IH'))
            #     else:
            #         ih = 1

            #     if ih == 1:
            #         single_count += 1
            #     else:
            #         multireads.add(read.qname)

        sys.stderr.write('Calculating normalization...')

        norm_val = None
        norm_val_orig = None

        if norm == 'all':
            norm_val_orig = _find_mapped_count(bam, whitelist, blacklist)
        elif norm == 'mapped':
            # norm_val_orig = single_count + len(multireads)
            norm_val_orig = total_count

        if norm_val_orig:
            norm_val = float(norm_val_orig) / 1000000

        sys.stderr.write('\n')

        out.write('## input%s%s\n' % (' ' if bam.filename else '', bam.filename))
        out.write('## model %s %s\n' % (self.get_name(), self.get_source()))
        out.write('## stranded %s\n' % stranded)
        out.write('## multiple %s\n' % multiple)
        if norm_val:
            out.write('## norm %s %s\n' % (norm, float(norm_val_orig)))
            out.write('## CPM-factor %s\n' % norm_val)

        out.write('#')
        out.write('\t'.join(self.get_headers()))
        out.write('\tlength\tcount')
        if norm_val:
            out.write('\tcount (CPM)')
            if fpkm:
                out.write('\tRPKM')

        out.write('\n')

        sortedkeys = []
        for k in repeats:
            sortedkeys.append(k)
        sortedkeys.sort()

        for class_level in [True, False]:
            for k in sortedkeys:
                if class_level and k[1] != '*':  # Do class level counts first
                    continue
                elif not class_level and k[1] == '*':
                    continue

                cols = [k[0], k[1], repeats[k]['size'], repeats[k]['count']]

                if norm_val:
                    cols.append(repeats[k]['count'] / norm_val)
                    if fpkm:
                        cols.append(repeats[k]['count'] / (repeats[k]['size'] / 1000.0) / norm_val)

                out.write('%s\n' % '\t'.join([str(x) for x in cols]))
