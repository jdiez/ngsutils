#!/usr/bin/env python
## category General
## desc Converts region mapping to genomic mapping
"""
Converts region mapping to genomic mapping

This takes a BAM file that has been mapped to a genomic region and converts
the mapping to genomic coordinates.  This can be used to convert reads mapped
against a junction library or targeted resequencing back to genomic
coordinates.  The names of the reference sequences should be named:
chrom:start-end (0-based start).  If there are gaps (junctions), they should
be named: chrom:start-end,start-end,etc...

In this is the case, this script will ensure the proper conversion of
reference, start position, and CIGAR alignment.

Example 1:
chr1:1000-2000    20    50M

converted to:
chr1    1020    50M

Example 2:
chr1:1000-1050,2000-2050,3000-4000    25    100M

converted to:
chr1    1025    25M950N50M950M25M

"""

import os
import sys
import pysam
import ngsutils.bam


def usage():
    print __doc__
    print """
Usage: bamutils convertregion {-overlap} in.bam out.bam chrom.sizes

Options:
-overlap    Require that all reads must overlap a splice junction
            by 4 bases. (Also removes unmapped reads)
"""
    sys.exit(1)


def bam_batch_reads(bam):
    '''
    Batches mapping for the same reads (qname) together, this way
    they can all be compared/converted together.
    '''
    reads = []
    last = None
    for read in bam:
        if last and read.qname != last:
            yield reads
            reads = []
        last = read.qname
        reads.append(read)

    if reads:
        yield reads


def bam_convertregion(infile, outfname, chrom_sizes, enforce_overlap=False, quiet=False):
    bamfile = pysam.Samfile(infile, "rb")
    header = bamfile.header
    header['SQ'] = []

    with open(chrom_sizes) as f:
        for line in f:
            if line[0] != '#':
                cols = line.strip().split('\t')
                header['SQ'].append({'LN': int(cols[1]), 'SN': cols[0]})

    outfile = pysam.Samfile('%s.tmp' % outfname, "wb", header=header)

    converted_count = 0
    invalid_count = 0
    unmapped_count = 0

    for batch in bam_batch_reads(bamfile):
        outreads = []

        for read in batch:
            if read.is_unmapped and not read.is_secondary:
                unmapped_count += 1
                if not enforce_overlap:
                    outfile.write(read)
                continue

            chrom, pos, cigar = ngsutils.bam.region_pos_to_genomic_pos(bamfile.getrname(read.tid), read.pos, read.cigar)

            read.pos = pos
            try:
                read.cigar = cigar
            except:
                print "Error trying to set CIGAR: %s to %s (%s, %s, %s)" % (read.cigar, cigar, read.qname, bamfile.getrname(read.tid), read.pos)

            chrom_found = False
            for i, name in enumerate(outfile.references):
                if name == chrom:
                    read.tid = i
                    chrom_found = True
                    break
            if not chrom_found:
                print "Can't find chrom: %s" % chrom
                sys.exit(1)

            if not enforce_overlap:
                outfile.write(read)
                continue

            valid, reason = ngsutils.bam.is_junction_valid(cigar)
            if valid:
                converted_count += 1
                outreads.append(read)
            else:
                invalid_count += 1

        if enforce_overlap and outreads:
            for i, read in enumerate(outreads):
                newtags = []
                for key, val in read.tags:
                    if key == 'HI':
                        newtags.append(('HI', i + 1))
                    elif key == 'IH':
                        newtags.append(('IH', len(outreads)))
                    else:
                        newtags.append((key, val))
                read.tags = newtags
                outfile.write(read)
        #
        # If a read doesn't overlap, just skip it in the output, don't reset the values
        #
        # else:
        #     read = pysam.AlignedRead()
        #     read.is_unmapped = True
        #     read.qname = batch[0].qname
        #     read.rname = -1
        #     read.mrnm = -1
        #     read.mpos = -1
        #     read.pos = -1
        #     read.mapq = 0
        #     read.isize = 0
        #     read.seq = batch[0].seq
        #     read.qual = batch[0].qual
        #     if batch[0].opt('CS'):
        #         # for some reason, pysam doesn't like it if you don't set them all at once
        #         read.tags = [('PG',batch[0].opt('PG')),('AS',2147483649),('NH',0),("IH",1),("HI",1),('CS',batch[0].opt('CS')),('CQ',batch[0].opt('CQ'))]
        #     else:
        #         read.tags = [('PG',batch[0].opt('PG')),('AS',2147483649),('NH',0),("IH",1),("HI",1),]
        #
        #     outfile.write(read)

    bamfile.close()
    outfile.close()

    if not quiet:
        sys.stderr.write("converted:%d\ninvalid:%d\nunmapped:%d\n" % (converted_count, invalid_count, unmapped_count))

    os.rename('%s.tmp' % outfname, outfname)


if __name__ == '__main__':
    infile = None
    outfile = None
    chrom_sizes = None
    overlap = False

    for arg in sys.argv[1:]:
        if arg == '-h':
            usage()
        elif arg == '-overlap':
            overlap = True
        elif not infile:
            infile = arg
        elif not outfile:
            outfile = arg
        elif not chrom_sizes:
            chrom_sizes = arg

    if not infile or not outfile or not chrom_sizes:
        usage()
    else:
        bam_convertregion(infile, outfile, chrom_sizes, overlap)
