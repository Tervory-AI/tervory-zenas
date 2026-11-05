"""Optional developer tool: rebuild the CJK TrueType face using fonttools==4.61.1."""
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.cu2quPen import Cu2QuPen
import sys
source=TTFont(sys.argv[1],fontNumber=2)
print('Source font:',source['name'].getDebugName(6),flush=True)
# Retain all encoded characters and their outlines, not just our test phrases.
from fontTools import subset
opts=subset.Options();opts.layout_features=[];opts.name_IDs=['*'];opts.name_legacy=True
sub=subset.Subsetter(options=opts);sub.populate(unicodes=source.getBestCmap());sub.subset(source)
order=source.getGlyphOrder();glyphs=source.getGlyphSet();converted={};upem=source['head'].unitsPerEm
for name in order:
    pen=TTGlyphPen(glyphs)
    glyphs[name].draw(Cu2QuPen(pen,max_err=upem/1000,reverse_direction=True))
    converted[name]=pen.glyph()
font=FontBuilder(upem,isTTF=True)
font.setupGlyphOrder(order);font.setupCharacterMap(source.getBestCmap());font.setupGlyf(converted)
font.setupHorizontalMetrics(source['hmtx'].metrics)
font.setupHorizontalHeader(ascent=source['hhea'].ascent,descent=source['hhea'].descent)
font.setupNameTable({'familyName':'Savings Unicode','styleName':'Regular','uniqueFontIdentifier':'SavingsUnicode-Regular-1', 'fullName':'Savings Unicode Regular','psName':'SavingsUnicode-Regular','version':'Version 1.0','copyright':source['name'].getDebugName(0)})
font.setupOS2(sTypoAscender=source['OS/2'].sTypoAscender,sTypoDescender=source['OS/2'].sTypoDescender,usWinAscent=source['OS/2'].usWinAscent,usWinDescent=source['OS/2'].usWinDescent)
font.setupPost();font.setupMaxp()
font.font['head'].created=source['head'].created
font.font['head'].modified=source['head'].modified
font.font.recalcTimestamp=False
font.save(str(Path(__file__).with_name('SavingsUnicode-Regular.ttf')))
