"""A made-up workflow the size of a real one, for the guide's layout: one container of 300+ tools and 150+ branches
whose outputs share upstream work, a lookup with preparation steps of its own, a Run Command after a save, steps
that feed no output, and a second container saved disabled. Names are invented; no rule has a decimal number, so a
step number (2.14) in the page's main text can be told apart from a value."""
from yx import WB

BOX = ("Daily checks",)
OLD = ("Old recon",)
FIELDS = ["Trade ID", "Desk", "Amount", "Ccy", "Status", "Trader", "Ref", "Notes", "Client Code", "Book"]

# rules: some say themselves in words, some do not
TESTS = ['[Amount] > %d', '[Desk] = "Desk %d"', 'IsEmpty([Trader])', 'Left([Ref], 2) = "X%d"',
         'Contains([Notes], "late %d")', '[Ccy] IN ("USD", "EUR")',
         'IF [Amount] > %d AND [Ccy] = "USD" OR [Status] = "Hold" THEN True ELSE [Book] = "B" ENDIF',
         'Right([Trade ID], 2) = [Book]']


def _test(n):
    t = TESTS[n % len(TESTS)]
    return t % (100 + n) if "%d" in t else t


def stress():
    wb = WB()
    wb.container(list(BOX), 9001)
    wb.container(list(OLD), 9002, disabled=True)
    nxt = [10]

    def nid():
        nxt[0] += 1
        return nxt[0]

    b = BOX
    # shared base: every output starts from it
    trades = wb.input(nid(), "Source\\Trades.csv", dict((f, "V_String") for f in FIELDS), box=b,
                      CodePage=65001, Delimeter=",", HeaderRow="True")
    clean = wb.cleanse(nid(), ["Trade ID", "Desk", "Trader"], box=b)
    key = wb.formula(nid(), [("Key", '[Trade ID] + "-" + [Ccy]'), ("Ref", 'Replace([Ref], ".xlsx", "")')], box=b)
    clients = wb.input(nid(), "Config\\Config.xlsx|||`Clients$`", box=b)
    csel = wb.select(nid(), [("Code", True, "Client Code"), ("Name", True, "Client"), ("*Unknown", False)], box=b)
    join = wb.join(nid(), ["Client Code"], ["Client Code"], box=b)
    base = wb.select(nid(), [("Notes", False), ("*Unknown", True)], box=b)
    wb.notes = getattr(wb, "notes", {})
    wb.conn(trades, clean); wb.conn(clean, key); wb.conn(key, join, da="Left")
    wb.conn(clients, csel); wb.conn(csel, join, da="Right"); wb.conn(join, base, sa="Join")
    _note(wb, base, "Clean trades")

    # output 1: 50 checks, rows that pass each one stacked, then saved, then a script formats the file
    u1 = wb.union(nid(), box=b)
    for n in range(50):
        f = wb.filter(nid(), _test(n), box=b)
        m = wb.formula(nid(), [("Issue", '"Check %d"' % n)], box=b)
        s = wb.select(nid(), [("Key", True), ("Desk", True), ("Issue", True), ("*Unknown", False)], box=b)
        wb.conn(base, f); wb.conn(f, m, sa="True"); wb.conn(m, s); wb.conn(s, u1, name="#%d" % (n + 1))
    srt = wb.sort(nid(), [("Key", "Ascending")], box=b)
    bud = wb.node(nid(), "BlockUntilDone", "", b)
    out1 = wb.output(nid(), "Output\\Exceptions.xlsx|||Exceptions", box=b, OutputOption="Overwrite")
    run = wb.run_command(nid(), "cscript.exe", "FormatAll.vbs Exceptions.xlsx", box=b)
    wb.conn(u1, srt); wb.conn(srt, bud); wb.conn(bud, out1, sa="Output1"); wb.conn(bud, run, sa="Output2")

    # output 2: 35 checks that keep both sides, each side labelled, stacked and totalled by desk
    u2 = wb.union(nid(), box=b)
    for n in range(35):
        f = wb.filter(nid(), _test(n + 3), box=b)
        yes = wb.formula(nid(), [("Result", '"Break %d"' % n)], box=b)
        no = wb.formula(nid(), [("Result", '"Clean %d"' % n)], box=b)
        wb.conn(base, f); wb.conn(f, yes, sa="True"); wb.conn(f, no, sa="False")
        wb.conn(yes, u2, name="#%d" % (2 * n + 1)); wb.conn(no, u2, name="#%d" % (2 * n + 2))
    tot = wb.summarize(nid(), [("Desk", "GroupBy", "Desk"), ("Result", "GroupBy", "Result"), ("Trade ID", "Count", "Trades")], box=b)
    out2 = wb.output(nid(), "Output\\Breaks.xlsx|||By desk", box=b, OutputOption="Overwrite")
    wb.conn(u2, tot); wb.conn(tot, out2)

    # output 3: rates prepared on their own (a side input of four steps), matched in; both sides kept
    rates = wb.input(nid(), "Config\\Rates.xlsx|||`Rates$`", box=b)
    rf = wb.formula(nid(), [("Ccy", "Uppercase([Currency])")], box=b)
    rk = wb.filter(nid(), "!IsEmpty([Rate])", box=b)
    rs = wb.summarize(nid(), [("Ccy", "GroupBy", "Ccy"), ("Rate", "Max", "Rate")], box=b)
    rj = wb.join(nid(), ["Ccy"], ["Ccy"], box=b)
    usd = wb.formula(nid(), [("Amount USD", "[Amount] * [Rate]")], box=b)
    miss = wb.formula(nid(), [("Amount USD", "Null()")], box=b)
    u3 = wb.union(nid(), box=b)
    out3 = wb.output(nid(), "Output\\Breaks.xlsx|||In USD", box=b, OutputOption="Overwrite")
    wb.conn(rates, rf); wb.conn(rf, rk); wb.conn(rk, rs, sa="True"); wb.conn(rs, rj, da="Right")
    wb.conn(base, rj, da="Left"); wb.conn(rj, usd, sa="Join"); wb.conn(rj, miss, sa="Left")
    wb.conn(usd, u3, name="#1"); wb.conn(miss, u3, name="#2"); wb.conn(u3, out3)
    _note(wb, rk, "Usable rates")

    # outputs 4 and 5 share more than the base: open trades with an age, saved as they are and as totals
    op = wb.filter(nid(), '[Status] = "Open"', box=b)
    age = wb.formula(nid(), [("Age", 'DateTimeDiff(DateTimeToday(), [Trade Date], "days")', "Int32")], box=b)
    out4 = wb.output(nid(), "Output\\Open trades.csv", box=b)
    sm = wb.summarize(nid(), [("Desk", "GroupBy", "Desk"), ("Age", "Max", "Oldest")], box=b)
    out5 = wb.output(nid(), "Output\\Open totals.xlsx|||Totals", box=b, OutputOption="Overwrite")
    wb.conn(base, op); wb.conn(op, age, sa="True"); wb.conn(age, out4); wb.conn(age, sm); wb.conn(sm, out5)

    # output 6: ten more checks on one desk, each a three-step chain, in one table
    one = wb.filter(nid(), '[Desk] = "Desk 1"', box=b)
    wb.conn(base, one, sa="Output")
    u6 = wb.union(nid(), box=b)
    for n in range(10):
        f = wb.filter(nid(), _test(n + 7), box=b)
        m = wb.formula(nid(), [("Check", '"Desk check %d"' % n)], box=b)
        r = wb.record_id(nid(), "Row", 1, box=b)
        wb.conn(one, f, sa="True"); wb.conn(f, m, sa="True"); wb.conn(m, r); wb.conn(r, u6, name="#%d" % (n + 1))
    out6 = wb.output(nid(), "Output\\Desk one.xlsx|||Checks", box=b, OutputOption="Overwrite")
    wb.conn(u6, out6)

    # steps that feed no output: a sample someone left on the canvas, and a browse
    smp = wb.sample(nid(), "First", 10, box=b)
    look = wb.formula(nid(), [("Look", '"x"')], box=b)
    wb.conn(base, smp); wb.conn(smp, look); wb.conn(look, wb.browse(nid(), box=b))

    # the disabled container: an older version of the recon, kept but switched off
    o = OLD
    old_in = wb.input(nid(), "Output\\Exceptions.xlsx|||`Exceptions$`", box=o)
    prev = old_in
    for n in range(8):
        f = wb.formula(nid(), [("Old %d" % n, '"old"')], box=o)
        wb.conn(prev, f)
        prev = f
    wb.conn(prev, wb.output(nid(), "Output\\Old recon.xlsx|||Recon", box=o, OutputOption="Overwrite"))
    return wb


def _note(wb, tid, text):
    """Give an already added tool an annotation (as a person does on the canvas)."""
    for n, (box, xml) in enumerate(wb.items):
        if xml.startswith('<Node ToolID="%s">' % tid):
            wb.items[n] = (box, xml.replace('<DefaultAnnotationText />', '<AnnotationText>%s</AnnotationText>'
                                            '<DefaultAnnotationText />' % text, 1))
