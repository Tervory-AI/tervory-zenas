"""Client-copy CSV series and vector PDF charts, with an explicit public field list.

Callers must supply a single tenant's dashboard report and eligible job rows.
No arbitrary dictionaries or internal metadata are serialized.
"""
import csv
from decimal import Decimal
from io import StringIO, BytesIO
from functools import lru_cache
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
import textwrap

NOTE = 'same work assumed; quality comparison pending'
STANDARD = 'the standard model'
TOP = 'the top model'


def amount(value):
    return Decimal(str(value or 0))


def dollars(value):
    if value is None:
        return 'Not available'
    whole, _, fraction = format(amount(value), 'f').partition('.')
    return '$'+whole+'.'+fraction.rstrip('0').ljust(2, '0')


def cheaper(spent, baseline):
    actual, before = amount(spent), amount(baseline)
    if before <= 0:
        return 'No measured saving'
    percent = (before-actual)/before*100
    return (f'{percent:.1f}% cheaper ({before/actual:.2f}× cheaper)' if actual > 0
            else f'{percent:.1f}% cheaper; no AI cost (multiple unavailable)')


def headings(report, evidence):
    total = report['total']
    return [
        'Your savings | What you saved',
        f"Updated {report['as_of']} UTC. All amounts are in US dollars.",
        f"{cheaper(total['spent'],total['would_have_cost'])} vs {STANDARD}",
        f"{cheaper(total['top_spent'],total['top_cost'])} vs {TOP} · {dollars(total['top_saved'])} saved",
        f"{total['top_count']} of {total['work_count']} steps compared vs {TOP}",
        *([NOTE] if total['quality_pending'] else []),
        'Only work with a checked comparison and a linked job is included.',
        f"All graph totals cover this snapshot; Your work contains page {evidence['page']+1} only.",
        'More work pages are available.' if evidence['has_more'] else 'This is the last work page.',
        *(['No comparable work yet. Your savings will appear here when a checked comparison is available.']
          if not total['work_count'] else []),
    ]


def job_cells(row):
    return [row['job_title'] or 'Completed work', row['member'], row['day'],
            row['spent'], row['would_have_cost'], row['saved'], row['top_saved'],
            'No AI cost' if row['free_work'] else '', '' if row['quality_compared'] else NOTE]


JOB_HEADERS = ['Your work', 'Team member', 'Day (UTC)', 'Spent · the standard model comparison (USD)',
               'Baseline: the standard model (USD)', 'Saved vs the standard model (USD)',
               'Saved vs the top model (USD)', 'Done at no AI cost', 'Quality comparison']


def csv_copy(report, evidence):
    stream = StringIO(newline='')
    writer = csv.writer(stream)

    def write(cells):
        # Text is quoted even after whitespace; titles must never become formulas.
        writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in cells])

    for line in headings(report,evidence):
        write([line])
    write(['Spent and would have cost', 'Day (UTC)', 'Spent · the standard model comparison (USD)', 'The standard model would have cost (USD)'])
    for row in report['daily']:
        write(['Spent and would have cost', row['day'], row['spent'], row['would_have_cost']])
    write(['Saved to date', 'Day (UTC)', 'Saved vs the standard model (USD)'])
    for row in report['daily']:
        write(['Saved to date', row['day'], row['cumulative']])
    total, month = report['total'], report['month']
    write(['Saved to date', 'Total vs the standard model (USD)',total['saved']])
    write(['Saved this month', 'Saved vs the standard model (USD)',month['saved'],
           'The standard model would have cost (USD)',month['would_have_cost'],cheaper(month['spent'],month['would_have_cost'])])
    write(['Done at no AI cost', 'Steps of work',total['free_count'],'Out of',total['work_count']])
    write(['Saved by team member','Team member','Saved vs the standard model (USD)'])
    for row in report['members']:
        write(['Saved by team member',row['member'],row['saved']])
    write([])
    write(JOB_HEADERS)
    for row in evidence['rows']:
        write(job_cells(row))
    return stream.getvalue()


@lru_cache(maxsize=1)
def unicode_fonts() -> tuple[str, ...]:
    names=[]
    for name,filename in [('SavingsSans','NotoSans-Regular.ttf'),('SavingsCJK','SavingsUnicode-Regular.ttf')]:
        pdfmetrics.registerFont(TTFont(name,str(Path(__file__).with_name('fonts')/filename)))
        names.append(name)
    return tuple(names)


def font_runs(value: str) -> list[tuple[str, str]]:
    runs=[]
    for character in value:
        name=next((name for name in unicode_fonts() if ord(character) in pdfmetrics.getFont(name).face.charToGlyph),None)
        if name is None:
            name=unicode_fonts()[0]
            character='\ufffd'
        if runs and runs[-1][0]==name:
            runs[-1]=(name,runs[-1][1]+character)
        else:
            runs.append((name,character))
    return runs


def text_width(value: str, size: int) -> float:
    return sum(pdfmetrics.stringWidth(text,font,size) for font,text in font_runs(value))


class PDF:
    """Small paginated vector document; chart values remain exact in adjacent text."""
    def __init__(self, document_title='Tervory savings'):
        self.document_title=document_title
        self.substituted=False
        self.pages = []
        self.commands = []
        self.y = 760

    def page(self):
        if self.commands:
            self.pages.append(self.commands)
        self.commands = []
        self.y = 760

    def space(self, height):
        if self.y-height < 35:
            self.page()

    def text(self, value, size=10):
        self.substituted |= ''.join(text for _,text in font_runs(str(value))) != str(value)
        # Wrap using the embedded font's real advances, including full-width CJK.
        for paragraph in str(value).splitlines() or ['']:
            lines=textwrap.wrap(paragraph,width=max(1,int(545/(size*.55)))) or ['']
            for line in lines:
                while line:
                    count=len(line)
                    while count>1 and text_width(line[:count],size)>545:
                        count-=1
                    self.space(size+7)
                    x=32
                    for font,text in font_runs(line[:count]):
                        self.commands.append((text,size,self.y,font,x))
                        x+=pdfmetrics.stringWidth(text,font,size)
                    self.y-=size+7
                    line=line[count:]
                if not line and not paragraph:
                    self.space(size+7)
                    self.y-=size+7

    def title(self, value):
        self.space(155)
        self.y -= 10
        self.text(value,14)

    def line_chart(self, points, fields):
        height, width = 88, 530
        self.space(height+45)
        maximum = max((amount(p[f]) for p in points for f in fields),default=Decimal(0))
        self.text(f'0 to {dollars(maximum)} · {STANDARD} comparison',9)
        bottom = self.y-height
        self.commands.append(f'.72 .75 .82 RG .5 w 32 {bottom} m 562 {bottom} l S')
        for n, field in enumerate(fields):
            vertices = [(32+i*width/max(1,len(points)-1),bottom+(float(amount(p[field])/maximum)*height if maximum else 0)) for i,p in enumerate(points)]
            self.commands.append(('[]' if n==0 else '[4 3]')+' 0 d 0 .094 .25 RG 1.5 w')
            if len(vertices)==1:
                x,y=vertices[0]
                self.commands.append(f'{x:.2f} {y:.2f} 3 3 re S')
            elif vertices:
                self.commands.append(' '.join(f'{x:.2f} {y:.2f} '+('m' if i==0 else 'l') for i,(x,y) in enumerate(vertices))+' S')
        self.commands.append('[] 0 d')
        self.y = bottom-15
        if points:
            self.text(f"{points[0]['day']} to {points[-1]['day']} (UTC)",9)
        else:
            self.text('No comparable work yet.',9)

    def bar(self, value, maximum):
        self.space(20)
        width = float(amount(value)/amount(maximum))*530 if amount(maximum) else 0
        self.commands.append(f'.96 .96 .98 rg 32 {self.y-8} 530 8 re f')
        if width:
            self.commands.append(f'0 .094 .25 rg 32 {self.y-8} {width:.3f} 8 re f')
        self.y -= 22

    def finish(self):
        if self.substituted:
            self.commands.append(("Some characters can't be shown in the PDF; the CSV has the full text",
                                  8,18,unicode_fonts()[0],32))
        self.page()
        output=BytesIO()
        canvas=Canvas(output,pagesize=(612,792),pageCompression=1)
        canvas.setTitle(self.document_title)
        canvas.setAuthor('Tervory')
        for commands in self.pages:
            for command in commands:
                if isinstance(command,str):
                    canvas.addLiteral(command)
                else:
                    value,size,y,font,x=command
                    canvas.setFillColorRGB(0,.094,.25)
                    canvas.setFont(font,size)
                    canvas.drawString(x,y,value)
            canvas.showPage()
        canvas.save()
        return output.getvalue()


def pdf_copy(report,evidence,client_name='Client savings'):
    pdf=PDF(client_name)
    for i,line in enumerate(headings(report,evidence)):
        pdf.text(line,16 if i==0 else 10)
    total,month=report['total'],report['month']
    pdf.title('Spent and would have cost')
    pdf.text(f"Spent {dollars(total['spent'])} · {STANDARD} comparison; would have cost {dollars(total['would_have_cost'])}")
    pdf.text('Solid: spent · Dashed: would have cost',9)
    pdf.line_chart(report['daily'],['spent','would_have_cost'])
    pdf.title('Saved to date')
    pdf.text(f"{dollars(total['saved'])} vs {STANDARD} · {total['jobs_count']} jobs · {total['work_count']} steps of work")
    pdf.line_chart(report['daily'],['cumulative'])
    pdf.title('Saved this month')
    pdf.text(f"{dollars(month['saved'])} vs {STANDARD} · {cheaper(month['spent'],month['would_have_cost'])}")
    pdf.bar(month['saved'],month['would_have_cost'])
    pdf.title('Done at no AI cost')
    pdf.text(f"{total['free_count']} of {total['work_count']} steps of work done by our own tools or reused answers.")
    pdf.bar(total['free_count'],total['work_count'])
    pdf.title('Saved by team member')
    maximum=max((amount(p['saved']) for p in report['members']),default=Decimal(0))
    for row in report['members']:
        pdf.space(60)
        pdf.text(f"{row['member']}: {dollars(row['saved'])} vs {STANDARD}")
        pdf.bar(row['saved'],maximum)
    if not report['members']:
        pdf.text('No comparable work yet.')
    pdf.page()
    pdf.text(f"Your work · Page {evidence['page']+1}",16)
    for row in evidence['rows']:
        pdf.space(165)
        cells=job_cells(row)
        pdf.text(cells[0],12)
        pdf.text(f'{cells[1]} · {cells[2]}'+(' · No AI cost' if row['free_work'] else ''))
        for label,value in zip(JOB_HEADERS[3:7],cells[3:7]):
            pdf.text(f'{label}: {dollars(value)}')
        if not row['quality_compared']:
            pdf.text(NOTE)
        pdf.y -= 12
    if not evidence['rows']:
        pdf.text('No matching work yet.')
    return pdf.finish()
