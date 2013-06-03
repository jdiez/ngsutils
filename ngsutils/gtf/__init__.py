'''
GTF gene models

See: http://mblab.wustl.edu/GTF22.html

GTF is a 1-based format, meaning that start sites start counting at 1. We
read in those start sites and subtract one so that the internal representation
is 0-based.

Notes:
    All internal positions returned are 0-based.
    If a transcript is missing a CDS or exon region, the (start, end) is used.
    If a transcript is missing a start/stop codon the start/end is used (+/- 3 bases)

    This class assumes a GTF format file. GFF3 isn't supported (yet).
'''


import sys
import os
from ngsutils.support.ngs_utils import gzip_aware_open
from ngsutils.support import symbols
from eta import ETA
import datetime

try:
    import cPickle as pickle
except:
    import pickle


class GTF(object):
    _version = 1

    def __init__(self, filename=None, cache_enabled=True, quiet=False, fileobj=None):
        if not filename and not fileobj:
            raise ValueError('Must pass either a filename or a fileobj')

        if fileobj:
            fobj = fileobj
            cache_enabled = False
            eta = None
        else:
            fobj = gzip_aware_open(filename)
            eta = ETA(os.stat(filename).st_size, fileobj=fobj)
            cachefile = os.path.join(os.path.dirname(filename), '.%s.cache' % os.path.basename(filename))

        self._genes = {}
        self._pos = 0
        self._gene_order = {}
        self._gene_names = {}
        self._gene_ids = {}
        warned = False

        if cache_enabled and os.path.exists(cachefile):
            self._load_cache(cachefile)

        if not self._genes:
            if not quiet:
                sys.stderr.write('Reading GTF file...\n')
            for line in fobj:
                try:
                    idx = line.find('#')
                    if idx > -1:
                        if idx == 0:
                            continue
                        line = line[:-idx]
                    chrom, source, feature, start, end, score, strand, frame, attrs = line.rstrip().split('\t')
                    source = symbols[source]
                    start = int(start) - 1  # Note: 1-based
                    end = int(end)
                    attributes = {}
                    for key, val in [x.split(' ', 1) for x in [x.strip() for x in attrs.split(';')] if x]:
                        if val[0] == '"' and val[-1] == '"':
                            val = val[1:-1]
                        attributes[key] = val

                    gid = None
                    if 'isoform_id' in attributes:
                        gid = attributes['isoform_id']
                    else:
                        gid = attributes['gene_id']
                        if not warned and not quiet:
                            sys.stderr.write('\nGTF file missing isoform annotation! Each transcript will be treated separately. (%s)\n' % gid)
                            warned = True
                    if eta:
                        eta.print_status(extra=gid)
                except:
                    import traceback
                    sys.stderr.write('Error parsing line:\n%s\n' % line)
                    traceback.print_exc()
                    sys.exit(1)

                if not gid in self._genes or chrom != self._genes[gid].chrom:
                    self._genes[gid] = _GTFGene(gid, chrom, source, **attributes)
                    if 'gene_name' in attributes:
                        gene_name = attributes['gene_name']
                        if not gene_name in self._gene_names:
                            self._gene_names[gene_name] = [gid]
                        else:
                            self._gene_names[gene_name].append(gid)

                        if gid != attributes['gene_id']:
                            self._gene_ids[attributes['gene_id']] = gid

                self._genes[gid].add_feature(attributes['transcript_id'], feature, start, end, strand)

            if eta:
                eta.done()

            if filename and fobj != sys.stdin:
                fobj.close()

            for gid in self._genes:
                gene = self._genes[gid]
                if not gene.chrom in self._gene_order:
                    self._gene_order[gene.chrom] = []
                self._gene_order[gene.chrom].append((gene.start, gid))

            for chrom in self._gene_order:
                self._gene_order[chrom].sort()

            if cache_enabled:
                self._write_cache(cachefile)

    def _load_cache(self, cachefile):
        sys.stderr.write('Reading GTF file (cached)...')
        started_t = datetime.datetime.now()
        try:
            with open(cachefile) as cache:
                version, genes, gene_order, gene_names, gene_ids = pickle.load(cache)
                if version == GTF._version:
                    self._genes, self._gene_order = genes, gene_order
                    self._gene_names = gene_names
                    self._gene_ids = gene_ids
                    sys.stderr.write('(%s sec)\n' % (datetime.datetime.now() - started_t).seconds)
                else:
                    sys.stderr.write('Error reading cached file... Processing original file.\n')
        except:
            self._genes = {}
            self._gene_order = {}
            sys.stderr.write('Failed reading cache! Processing original file.\n')

    def _write_cache(self, cachefile):
        sys.stderr.write('(saving GTF cache)...')
        with open(cachefile, 'w') as cache:
            pickle.dump((GTF._version, self._genes, self._gene_order, self._gene_names, self._gene_ids), cache)
        sys.stderr.write('\n')

    def fsize(self):
        return len(self._genes)

    def tell(self):
        return self._pos

    def find(self, chrom, start, end=None, strand=None):
        if chrom not in self._gene_order:
            return

        if not end:
            end = start

        if end < start:
            raise ValueError('[gtf.find] Error: End must be smaller than start!')

        for g_start, gid in self._gene_order[chrom]:
            g_start = self._genes[gid].start
            g_end = self._genes[gid].end
            g_strand = self._genes[gid].strand

            if strand and g_strand != strand:
                continue

            if g_start <= start <= g_end or g_start <= end <= g_end:
                # gene spans the query boundary
                yield self._genes[gid]
            elif start <= g_start <= end and start <= g_end <= end:
                # gene is completely inside boundary
                yield self._genes[gid]
        return

    def get_by_id(self, gene_id):
        if gene_id in self._gene_ids:
            return self._genes[self._gene_ids[gene_id]]
        elif gene_id in self._genes:
            return self._genes[gene_id]

        return None

    def get_by_name(self, gene_name):
        if gene_name in self._gene_names:
            for g in self._genes[self._gene_names[gene_name]]:
                yield g

    @property
    def genes(self):
        self._pos = 0
        for chrom in sorted(self._gene_order):
            for start, gene_id in self._gene_order[chrom]:
                yield self._genes[gene_id]
                self._pos += 1


class _GTFGene(object):
    """
    Stores info for a single gene_id

    A gene consists of one or more transcripts. It *must* have a gene_id, and chrom
    It may also contain a gene_name and an isoform_id.

    The gid is the 'gene_id' from the GTF file *unless* there is an isoform_id
    attribute. If 'isoform_id' is present, this is used for the gid.


    """

    def __init__(self, gid, chrom, source, gene_id, gene_name=None, isoform_id=None, **extras):
        self.gid = gid
        self.gene_id = gene_id
        self.gene_name = gene_name if gene_name else gene_id
        self.isoform_id = isoform_id if isoform_id else gene_id

        self.chrom = chrom
        self.source = source

        self._transcripts = {}
        self._regions = []

        self.start = None
        self.end = None
        self.strand = None

    def __repr__(self):
        return '%s(%s) %s:%s-%s[%s]' % (self.gene_name, self.gid, self.chrom, self.start, self.end, self.strand)

    @property
    def transcripts(self):
        for t in self._transcripts:
            yield self._transcripts[t]

    def add_feature(self, transcript_id, feature, start, end, strand):
        if not transcript_id in self._transcripts:
            self._transcripts[transcript_id] = _GTFTranscript(transcript_id, strand)

        t = self._transcripts[transcript_id]

        # this start/end will cover the entire transcript.
        # this way if there is only a 'gene' annotation,
        # we can still get a gene/exon start/end
        if self.start is None or start < self.start:
            self.start = start
        if self.end is None or end > self.end:
            self.end = end
        if self.strand is None:
            self.strand = strand

        if t.start is None or start < t.start:
            t.start = start
        if t.end is None or end > t.end:
            t.end = end

        if feature == 'exon':
            t._exons.append((start, end))
        elif feature == 'CDS':
            t._cds.append((start, end))
        elif feature == 'start_codon':
            t._start_codon = (start, end)
        elif feature == 'stop_codon':
            t._stop_codon = (start, end)
        else:
            # this is an unsupported feature - possibly add a debug message
            pass

    @property
    def regions(self):
        # these are potentially memory-intensive, so they are calculated on the
        # fly when needed.
        if not self._regions:
            all_starts = []
            all_ends = []
            tids = []

            for tid in self._transcripts:
                tids.append(tid)
                starts = []
                ends = []
                for start, end in self._transcripts[tid].exons:
                    starts.append(start)
                    ends.append(end)
                all_starts.append(starts)
                all_ends.append(ends)

            self._regions = calc_regions(self.start, self.end, tids, all_starts, all_ends)

        i = 0
        for start, end, const, names in self._regions:
            i += 1
            yield (i, start, end, const, names)


class _GTFTranscript(object):
    def __init__(self, transcript_id, strand):
        self.transcript_id = transcript_id
        self.strand = strand
        self._exons = []
        self._cds = []
        self._start_codon = None
        self._stop_codon = None

        self.start = None
        self.end = None

    def __repr__(self):
        return '<transcript id="%s" strand="%s" start="%s" end="%s" exons="%s">' % (self.transcript_id, self.strand, self.start, self.end, ','.join(['%s->%s' % (s, e) for s, e in self.exons]))

    @property
    def exons(self):
        if self._exons:
            return self._exons
        else:
            return [(self.start, self.end)]

    @property
    def cds(self):
        if self._cds:
            return self._cds
        else:
            return [(self.start, self.end)]

    @property
    def start_codon(self):
        if self._start_codon:
            return self._start_codon
        else:
            return (self.start, self.start + 3)

    @property
    def stop_codon(self):
        if self._stop_codon:
            return self._stop_codon
        else:
            return (self.end - 3, self.end)


def calc_regions(txStart, txEnd, kg_names, kg_starts, kg_ends):
    '''
        This takes a list of start/end positions (one set per isoform)

        It splits these into regions by assigning each base in the
        txStart-txEnd range a number.  The number is a bit-mask representing
        each isoform that includes that base in a coding region.  Each
        isoform has a number 2^N, so the bit-mask is just a number.

        Next, it scans the map for regions with the same bit-mask.
        When a different bitmask is found, the previous region (if not intron)
        is added to the list of regions.

        Returns a list of tuples:
        (start,end,is_const,names) where names is a comma-separated string
                                   of accns that make up the region

    Test:
        foo: 100->110, 125->135, 150->160, 175->200
        bar: 100->110, 125->135,           175->200
        baz: 100->110,           150->160, 175->200

    >>> list(calc_regions(100, 200, ['foo', 'bar', 'baz'], [[100, 125, 150, 175], [100, 125, 175], [100, 150, 175]], [[110, 135, 160, 200], [110, 135, 200], [110, 160, 200]]))
    [(100, 110, True, 'foo,bar,baz'), (125, 135, False, 'foo,bar'), (150, 160, False, 'foo,baz'), (175, 200, True, 'foo,bar,baz')]

    # overhangs...
        baz: 100->120,           150->160, 170->200

    >>> list(calc_regions(100, 200, ['foo', 'bar', 'baz'], [[100, 125, 150, 175], [100, 125, 175], [100, 150, 170]], [[110, 135, 160, 200], [110, 135, 200], [120, 160, 200]]))
    [(100, 110, True, 'foo,bar,baz'), (110, 120, False, 'baz'), (125, 135, False, 'foo,bar'), (150, 160, False, 'foo,baz'), (170, 175, False, 'baz'), (175, 200, True, 'foo,bar,baz')]

        foo: 100->110, 120->130, 140->150
        bar: 100->110,           140->150

    >>> list(calc_regions(100, 150, ['foo', 'bar'], [[100, 120, 140], [100, 140,]], [[110, 130, 150], [110, 150]]))
    [(100, 110, True, 'foo,bar'), (120, 130, False, 'foo'), (140, 150, True, 'foo,bar')]

        foo: 100->110, 120->130, 140->150
        bar: 100->115,           140->150 # 3' overhang

    >>> list(calc_regions(100, 150, ['foo', 'bar'], [[100, 120, 140], [100, 140,]], [[110, 130, 150], [115, 150]]))
    [(100, 110, True, 'foo,bar'), (110, 115, False, 'bar'), (120, 130, False, 'foo'), (140, 150, True, 'foo,bar')]

        foo: 100->110, 120->130, 140->150
        bar: 100->110,           135->150 # 5' overhang

    >>> list(calc_regions(100, 150, ['foo', 'bar'], [[100, 120, 140], [100, 135,]], [[110, 130, 150], [110, 150]]))
    [(100, 110, True, 'foo,bar'), (120, 130, False, 'foo'), (135, 140, False, 'bar'), (140, 150, True, 'foo,bar')]


    '''

    maskmap = [0, ] * (txEnd - txStart)
    mask = 1

    mask_start_end = {}
    mask_names = {}

    for name, starts, ends in zip(kg_names, kg_starts, kg_ends):
        mask_start = None
        mask_end = None
        mask_names[mask] = name

        starts = [int(x) for x in starts]
        ends = [int(x) for x in ends]

        for start, end in zip(starts, ends):
            if not mask_start:
                mask_start = int(start)
            mask_end = int(end)

            for i in xrange(start - txStart, end - txStart):
                maskmap[i] = maskmap[i] | mask

        mask_start_end[mask] = (mask_start, mask_end)
        mask = mask * 2

    # print mask_start_end
    # print maskmap

    last_val = 0
    regions = []
    region_start = 0

    def _add_region(start, end, value):
        rstart = start + txStart
        rend = end + txStart
        const = True
        names = []

        '''
        This code only calls a region alt, if the transcript actually spans this region.

        example - two transcripts:

        1)        xxxxxx------xxxx------xxx---xxxx--xxxxxxxxx
        2)           xxx------xxxx--xxx-------xxxx--xxxxxxxxxxxxx
        const/alt cccccc------cccc--aaa-aaa---cccc--ccccccccccccc

        I'm not sure if this is a good idea or not... What this *should* be is:

        1)        xxxxxx------xxxx------xxx---xxxx----xxxxxxxxx
        2)           xxx------xxxx--xxx-------xxxx----xxxxxxxxxxxxx
        3)           xxx------xxxx--xxx-------xxxxxa
        const/alt aaaccc------cccc--aaa-aaa---ccccaa--cccccccccaaaa

        Where alt-5 and alt-3 are only dictated by transcripts that contain them...
        There should be extra code to handle this situation...

        ## TODO: Add alt-5/alt-3 code
        '''

        # this keeps track of which transcripts go into each region
        # and if it is const or alt
        for mask in mask_start_end:
            mstart, mend = mask_start_end[mask]
            if rstart >= mstart and rend <= mend:
                if value & mask == 0:
                    const = False
                else:
                    names.append(mask_names[mask])

        regions.append((rstart, rend, const, ','.join(names)))

    for i in xrange(0, len(maskmap)):
        if maskmap[i] == last_val:
            continue

        if last_val:
            _add_region(region_start, i, last_val)

            region_start = i
        else:
            region_start = i

        last_val = maskmap[i]

    if last_val:
        _add_region(region_start, i + 1, last_val)  # extend by one...

    return regions
