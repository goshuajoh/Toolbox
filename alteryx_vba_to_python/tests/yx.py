"""Builds synthetic .yxmd text with the XML shapes Alteryx Designer writes. Test helper only."""
from xml.sax.saxutils import escape, quoteattr as q

GUI = {"Input": "AlteryxBasePluginsGui.DbFileInput.DbFileInput", "Output": "AlteryxBasePluginsGui.DbFileOutput.DbFileOutput",
       "Select": "AlteryxBasePluginsGui.AlteryxSelect.AlteryxSelect", "Formula": "AlteryxBasePluginsGui.Formula.Formula",
       "Filter": "AlteryxBasePluginsGui.Filter.Filter", "Join": "AlteryxBasePluginsGui.Join.Join",
       "Union": "AlteryxBasePluginsGui.Union.Union", "Summarize": "AlteryxSpatialPluginsGui.Summarize.Summarize",
       "Sort": "AlteryxBasePluginsGui.Sort.Sort", "Unique": "AlteryxBasePluginsGui.Unique.Unique",
       "Sample": "AlteryxBasePluginsGui.Sample.Sample", "RecordID": "AlteryxBasePluginsGui.RecordID.RecordID",
       "Transpose": "AlteryxBasePluginsGui.Transpose.Transpose",
       "MultiFieldFormula": "AlteryxBasePluginsGui.MultiFieldFormula.MultiFieldFormula",
       "FindReplace": "AlteryxBasePluginsGui.FindReplace.FindReplace",
       "AppendFields": "AlteryxBasePluginsGui.AppendFields.AppendFields",
       "DateTime": "AlteryxBasePluginsGui.DateTime.DateTime", "TextInput": "AlteryxBasePluginsGui.TextInput.TextInput",
       "RunCommand": "AlteryxBasePluginsGui.RunCommand.RunCommand", "Message": "AlteryxBasePluginsGui.Message.Message",
       "Directory": "AlteryxBasePluginsGui.Directory.Directory",
       "DynamicInput": "AlteryxBasePluginsGui.DynamicInput.DynamicInput",
       "Browse": "AlteryxBasePluginsGui.BrowseV2.BrowseV2", "TextBox": "AlteryxGuiToolkit.TextBox.TextBox",
       "MultiRowFormula": "AlteryxBasePluginsGui.MultiRowFormula.MultiRowFormula"}


def _sel(fields):
    out = ["<SelectFields>"]
    for f in fields:
        name, selected, rename, typ, size = (list(f) + [None] * 5)[:5] if not isinstance(f, str) else [f, True, None, None, None]
        attrs = "field=%s selected=%s" % (q(name), q("True" if selected in (True, None) else "False"))
        for k, v in (("rename", rename), ("type", typ), ("size", size)):
            if v:
                attrs += " %s=%s" % (k, q(str(v)))
        out.append("<SelectField %s />" % attrs)
    return "".join(out) + "</SelectFields>"


class WB:
    def __init__(self):
        self.items, self.conns = [], []          # items: (container path tuple, xml)
        self.boxes = {}                          # path -> (id, disabled)

    def container(self, path, tid, disabled=False):
        self.boxes[tuple(path)] = (tid, disabled)

    def node(self, tid, kind, cfg, box=(), meta=None, macro=None, note=""):
        gui = '<GuiSettings Plugin=%s><Position x="54" y="54" /></GuiSettings>' % q(GUI[kind]) if not macro else \
            '<GuiSettings><Position x="54" y="54" /></GuiSettings>'
        m = ""
        for anchor, fields in (meta or {}).items():
            m += '<MetaInfo connection=%s><RecordInfo>%s</RecordInfo></MetaInfo>' % (q(anchor), "".join(
                '<Field name=%s type=%s size="254" />' % (q(n), q(t)) for n, t in fields))
        eng = '<EngineSettings Macro=%s />' % q(macro) if macro else '<EngineSettings EngineDll="AlteryxBasePluginsEngine.dll" />'
        self.items.append((tuple(box), '<Node ToolID="%s">%s<Properties><Configuration>%s</Configuration>'
                           '<Annotation DisplayMode="0"><Name />%s<DefaultAnnotationText /><Left value="False" />'
                           '</Annotation>%s</Properties>%s</Node>' % (
                               tid, gui, cfg, "<AnnotationText>%s</AnnotationText>" % escape(note) if note else "", m, eng)))
        return tid

    def conn(self, src, dst, sa="Output", da="Input", name=None):
        self.conns.append('<Connection%s><Origin ToolID="%s" Connection=%s /><Destination ToolID="%s" Connection=%s />'
                          '</Connection>' % (" name=%s" % q(name) if name else "", src, q(sa), dst, q(da)))

    # ---- one method per tool kind
    def input(self, tid, file, types=None, box=(), **opts):
        o = "".join("<%s>%s</%s>" % (k, escape(str(v)), k) for k, v in opts.items())
        meta = {"Output": list(types.items())} if types else None
        return self.node(tid, "Input", '<Passwords /><File OutputFileName="" FileFormat="0">%s</File>'
                         '<FormatSpecificOptions>%s</FormatSpecificOptions>' % (escape(file), o), box, meta)

    def output(self, tid, file, box=(), **opts):
        o = "".join("<%s>%s</%s>" % (k, escape(str(v)), k) for k, v in opts.items())
        return self.node(tid, "Output", '<File MaxRecords="" FileFormat="25">%s</File><Passwords />'
                         '<FormatSpecificOptions>%s</FormatSpecificOptions><MultiFile value="False" />' % (escape(file), o), box)

    def text_input(self, tid, columns, rows, types=None, box=()):
        cfg = '<NumRows value="%d" /><Fields>%s</Fields><Data>%s</Data>' % (
            len(rows), "".join("<Field name=%s />" % q(c) for c in columns),
            "".join("<r>%s</r>" % "".join("<c />" if c is None else "<c>%s</c>" % escape(str(c)) for c in r) for r in rows))
        return self.node(tid, "TextInput", cfg, box, {"Output": list(types.items())} if types else None)

    def select(self, tid, fields, box=()):
        return self.node(tid, "Select", '<OrderChanged value="False" /><CommaDecimal value="False" />' + _sel(fields), box)

    def formula(self, tid, fields, box=(), note=""):
        """fields: [(name, expression)] or [(name, expression, type)]"""
        cfg = "<FormulaFields>%s</FormulaFields>" % "".join(
            '<FormulaField expression=%s field=%s size="1073741823" type=%s />' % (
                q(f[1]), q(f[0]), q(f[2] if len(f) > 2 else "V_WString")) for f in fields)
        return self.node(tid, "Formula", cfg, box, note=note)

    def filter(self, tid, expression=None, simple=None, box=()):
        if simple:
            field, op, operand = simple
            cfg = ('<Mode>Simple</Mode><Simple><Operator>%s</Operator><Field>%s</Field><Operands>'
                   '<IgnoreTimeInDateTime>True</IgnoreTimeInDateTime><DateType>fixed</DateType>'
                   '<Operand>%s</Operand></Operands></Simple>' % (escape(op), escape(field), escape(operand)))
        else:
            cfg = "<Mode>Custom</Mode><Expression>%s</Expression>" % escape(expression)
        return self.node(tid, "Filter", cfg, box)

    def join(self, tid, left, right, fields=None, box=()):
        cfg = ('<JoinInfo connection="Left">%s</JoinInfo><JoinInfo connection="Right">%s</JoinInfo>'
               '<SelectConfiguration><Configuration outputConnection="Join"><OrderChanged value="False" />'
               '<CommaDecimal value="False" />%s</Configuration></SelectConfiguration>' % (
                   "".join("<Field field=%s />" % q(f) for f in left), "".join("<Field field=%s />" % q(f) for f in right),
                   _sel(fields or [("*Unknown", True)])))
        return self.node(tid, "Join", cfg, box)

    def append_fields(self, tid, fields=None, box=()):
        return self.node(tid, "AppendFields", '<CartesianMode>Allow</CartesianMode><SelectConfiguration>'
                         '<Configuration outputConnection="Output">%s</Configuration></SelectConfiguration>'
                         % _sel(fields or [("*Unknown", True)]), box)

    def union(self, tid, mode="ByName", order=None, box=()):
        cfg = "<ByName_ErrorMode>Warning</ByName_ErrorMode><ByName_OutputMode>All</ByName_OutputMode><Mode>%s</Mode>" % mode
        cfg += ('<SetOutputOrder value="True" /><OutputOrder>%s</OutputOrder>' % "".join(
            "<Connection>%s</Connection>" % o for o in order)) if order else '<SetOutputOrder value="False" />'
        return self.node(tid, "Union", cfg, box)

    def summarize(self, tid, fields, box=()):
        """fields: [(field, action, rename)] or with a 4th item: separator for Concat"""
        out = []
        for f in fields:
            inner = "<Concat_Start /><Separator>%s</Separator><Concat_End />" % escape(f[3]) if len(f) > 3 else ""
            out.append("<SummarizeField field=%s action=%s rename=%s>%s</SummarizeField>" % (q(f[0]), q(f[1]), q(f[2]), inner))
        return self.node(tid, "Summarize", "<SummarizeFields>%s</SummarizeFields>" % "".join(out), box)

    def sort(self, tid, fields, box=()):
        return self.node(tid, "Sort", '<SortInfo locale="0">%s</SortInfo>' % "".join(
            "<Field field=%s order=%s />" % (q(f), q(o)) for f, o in fields), box)

    def unique(self, tid, fields, box=()):
        return self.node(tid, "Unique", "<UniqueFields>%s</UniqueFields>" % "".join("<Field field=%s />" % q(f) for f in fields), box)

    def sample(self, tid, mode, n, group=(), box=()):
        return self.node(tid, "Sample", '<Mode>%s</Mode><N>%s</N><GroupFields orderChanged="False">%s</GroupFields>' % (
            mode, n, "".join("<Field name=%s />" % q(g) for g in group)), box)

    def record_id(self, tid, name="RecordID", start=1, typ="Int32", size="", box=()):
        return self.node(tid, "RecordID", "<FieldName>%s</FieldName><StartValue>%s</StartValue><FieldType>%s</FieldType>"
                         "<FieldSize>%s</FieldSize><Position>0</Position>" % (name, start, typ, size), box)

    def transpose(self, tid, keys, data, box=()):
        return self.node(tid, "Transpose", "<ErrorWarn>Warn</ErrorWarn><KeyFields>%s</KeyFields><DataFields>%s"
                         '<Field field="*Unknown" selected="False" /></DataFields>' % (
                             "".join("<Field field=%s />" % q(k) for k in keys),
                             "".join('<Field field=%s selected="True" />' % q(d) for d in data)), box)

    def multi_field(self, tid, fields, expression, group="Text", box=()):
        return self.node(tid, "MultiFieldFormula", '<FieldType>%s</FieldType><Fields orderChanged="False">%s'
                         '<Field name="*Unknown" selected="False" /></Fields><CopyOutput value="False" />'
                         '<NewFieldAddOn>New_</NewFieldAddOn><NewFieldAddOnPos>Prefix</NewFieldAddOnPos>'
                         '<ChangeFieldType value="False" /><Expression>%s</Expression>' % (
                             group, "".join("<Field name=%s />" % q(f) for f in fields), escape(expression)), box)

    def find_replace(self, tid, find, search, mode="FindAny", append=(), replace="", nocase=False, box=()):
        cfg = ('<FieldFind>%s</FieldFind><FieldSearch>%s</FieldSearch><ReplaceFoundField>%s</ReplaceFoundField>'
               '<FindMode>%s</FindMode><NoCase value="%s" /><MatchWholeWord value="False" />'
               '<ReplaceMode>%s</ReplaceMode><ReplaceMultipleFound value="False" /><ReplaceAppendFields>%s'
               '</ReplaceAppendFields>' % (escape(find), escape(search), escape(replace), mode, nocase,
                                           "Append" if append else "Replace",
                                           "".join("<Field field=%s />" % q(a) for a in append)))
        return self.node(tid, "FindReplace", cfg, box)

    def datetime(self, tid, field, fmt, out, box=()):
        return self.node(tid, "DateTime", '<IsFrom value="False" /><InputFieldName>%s</InputFieldName>'
                         "<Language>English</Language><Format>%s</Format><OutputFieldName>%s</OutputFieldName>" % (
                             escape(field), escape(fmt), escape(out)), box)

    def directory(self, tid, folder, spec="*.*", box=()):
        return self.node(tid, "Directory", '<Directory>%s</Directory><FileSpec>%s</FileSpec>'
                         '<IncludeSubDirs value="False" />' % (escape(folder), escape(spec)), box)

    def dynamic_input(self, tid, template, field, types=None, box=(), **opts):
        o = "".join("<%s>%s</%s>" % (k, escape(str(v)), k) for k, v in opts.items())
        return self.node(tid, "DynamicInput", '<InputConfiguration><Configuration><Passwords /><File FileFormat="0">%s'
                         '</File><FormatSpecificOptions>%s</FormatSpecificOptions></Configuration></InputConfiguration>'
                         '<Mode>ReadList</Mode><ReadList_Field>%s</ReadList_Field><ReadList_Type>FullPath</ReadList_Type>'
                         % (escape(template), o, escape(field)), box, {"Output": list(types.items())} if types else None)

    def cleanse(self, tid, fields, box=(), **boxes):
        vals = {"Check Box (135)": "False", "Check Box (136)": "False", "Check Box (84)": "False",
                "Check Box (117)": "False", "Check Box (15)": "True", "Check Box (109)": "False",
                "Check Box (122)": "False", "Check Box (53)": "False", "Check Box (58)": "False",
                "Check Box (70)": "False", "Check Box (77)": "False", "Drop Down (81)": "upper"}
        vals.update({k.replace("_", " ").replace("box ", "Check Box (") + ")": v for k, v in boxes.items()})
        cfg = '<Value name="List Box (11)">%s</Value>' % escape(",".join('"%s"' % f for f in fields))
        cfg += "".join("<Value name=%s>%s</Value>" % (q(k), v) for k, v in vals.items())
        return self.node(tid, "Formula", cfg, box, macro="Cleanse.yxmc")

    def pbud(self, tid, box=()):
        return self.node(tid, "Formula", "", box, macro="CReW_ParallelBlockUntilDone.yxmc")

    def run_command(self, tid, command, args="", box=()):
        return self.node(tid, "RunCommand", "<Command>%s</Command><CommandArguments>%s</CommandArguments>"
                         "<WorkingDirectory /><RunMinimized value=\"True\" />" % (escape(command), escape(args)), box)

    def message(self, tid, when, kind, text, flt="", box=()):
        return self.node(tid, "Message", "<When>%s</When><MessageType>%s</MessageType><MessageExpression>%s"
                         "</MessageExpression><Filter>%s</Filter>" % (when, kind, escape(text), escape(flt)), box)

    def browse(self, tid, box=()):
        return self.node(tid, "Browse", "<TempFile />", box)

    def text_box(self, tid, text, box=()):
        return self.node(tid, "TextBox", "<Text>%s</Text>" % escape(text), box)

    def raw(self, tid, kind, cfg, box=()):
        return self.node(tid, kind, cfg, box)

    # ---- document
    def _nodes(self, path):
        out = [x for p, x in self.items if p == path]
        for p, (tid, disabled) in self.boxes.items():
            if p[:-1] == path:
                out.append('<Node ToolID="%s"><GuiSettings Plugin="AlteryxGuiToolkit.ToolContainer.ToolContainer">'
                           '<Position x="1" y="1" width="9" height="9" /></GuiSettings><Properties><Configuration>'
                           '<Caption>%s</Caption><Style TextColor="#314c4a" /><Disabled value="%s" /><Folded value="False" />'
                           '</Configuration><Annotation DisplayMode="0"><Name /><DefaultAnnotationText />'
                           '<Left value="False" /></Annotation></Properties><ChildNodes>%s</ChildNodes></Node>' % (
                               tid, escape(p[-1]), disabled, "".join(self._nodes(p))))
        return out

    def xml(self):
        return ('<?xml version="1.0"?>\n<AlteryxDocument yxmdVer="2022.3"><Nodes>%s</Nodes><Connections>%s</Connections>'
                '<Properties><Memory default="True" /><MetaInfo><NameIsFileName value="True" /></MetaInfo></Properties>'
                '</AlteryxDocument>\n' % ("\n".join(self._nodes(())), "\n".join(self.conns)))
