import java.nio.file.*;
import java.util.*;
import org.eclipse.emf.ecore.EObject;
import org.omg.sysml.interactive.*;
import org.omg.sysml.lang.sysml.*;

/**
 * Parses and validates SysML v2 textual files, in the order given, in one shared model.
 * With --dump <file> it also writes the parsed elements as JSON lines for the model checks.
 */
public class Validate {
    static String q(Object o) {
        if (o == null) return "null";
        String s = o.toString();
        StringBuilder b = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c == '\n') b.append("\\n");
            else if (c == '\r') b.append("\\r");
            else if (c == '\t') b.append("\\t");
            else if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
            else b.append(c);
        }
        return b.append('"').toString();
    }

    static String qn(Element e) {
        if (e == null) return null;
        String n = e.getQualifiedName();
        return n != null ? n : e.getName();
    }

    static String fname(Feature f) {
        if (f == null) return null;
        if (!f.getChainingFeature().isEmpty()) {
            List<String> names = new ArrayList<>();
            for (Feature c : f.getChainingFeature()) names.add(c.getDeclaredName() != null ? c.getDeclaredName() : c.getName());
            Feature first = f.getChainingFeature().get(0);
            String owner = first.getOwner() != null ? qn(first.getOwner()) : null;
            return (owner == null ? "" : owner + "::") + String.join(".", names);
        }
        return qn(f);
    }

    static String list(Collection<String> xs) {
        StringBuilder b = new StringBuilder("[");
        boolean first = true;
        for (String x : xs) {
            if (x == null) continue;
            if (!first) b.append(',');
            b.append(q(x));
            first = false;
        }
        return b.append(']').toString();
    }

    static Object literal(Element expr) {
        if (expr instanceof LiteralString) return ((LiteralString) expr).getValue();
        if (expr instanceof LiteralInteger) return ((LiteralInteger) expr).getValue();
        if (expr instanceof LiteralBoolean) return ((LiteralBoolean) expr).isValue();
        if (expr instanceof LiteralRational) return ((LiteralRational) expr).getValue();
        if (expr instanceof FeatureReferenceExpression) {
            Feature f = ((FeatureReferenceExpression) expr).getReferent();
            return f == null ? null : f.getDeclaredName() != null ? f.getDeclaredName() : f.getName();
        }
        return null;
    }

    static String describe(Element e) {
        List<String> parts = new ArrayList<>();
        parts.add("\"kind\":" + q(e.eClass().getName()));
        parts.add("\"qn\":" + q(qn(e)));
        parts.add("\"name\":" + q(e.getDeclaredName()));
        parts.add("\"owner\":" + q(qn(e.getOwner())));
        if (!e.getDocumentation().isEmpty()) {
            parts.add("\"doc\":" + q(e.getDocumentation().get(0).getBody()));
        }
        if (e instanceof Feature) {
            Feature f = (Feature) e;
            List<String> types = new ArrayList<>();
            for (FeatureTyping t : f.getOwnedTyping()) types.add(qn(t.getType()));
            parts.add("\"types\":" + list(types));
            List<String> refs = new ArrayList<>();
            if (f.getOwnedReferenceSubsetting() != null) refs.add(fname(f.getOwnedReferenceSubsetting().getReferencedFeature()));
            for (Subsetting s : f.getOwnedSubsetting()) refs.add(qn(s.getSubsettedFeature()));
            parts.add("\"refs\":" + list(refs));
            for (Relationship r : f.getOwnedRelationship()) {
                if (r instanceof FeatureValue) {
                    Object v = literal(((FeatureValue) r).getValue());
                    if (v instanceof String) parts.add("\"value\":" + q(v));
                    else if (v != null) parts.add("\"value\":" + v);
                }
            }
            List<String> ends = new ArrayList<>();
            for (Feature c : f.getOwnedFeature()) {
                if (c.isEnd() && c.getOwnedReferenceSubsetting() != null) {
                    ends.add(fname(c.getOwnedReferenceSubsetting().getReferencedFeature()));
                } else if (c.isEnd() && !c.getOwnedSubsetting().isEmpty()) {
                    ends.add(fname(c.getOwnedSubsetting().get(0).getSubsettedFeature()));
                }
            }
            if (!ends.isEmpty()) parts.add("\"ends\":" + list(ends));
        }
        if (e instanceof Definition || e instanceof Usage) {
            List<String> supers = new ArrayList<>();
            if (e instanceof Type) {
                for (Specialization s : ((Type) e).getOwnedSpecialization()) {
                    if (!(s instanceof FeatureTyping)) supers.add(qn(s.getGeneral()));
                }
            }
            if (!supers.isEmpty()) parts.add("\"supers\":" + list(supers));
        }
        if (e instanceof SatisfyRequirementUsage) {
            SatisfyRequirementUsage sat = (SatisfyRequirementUsage) e;
            parts.add("\"satisfied\":" + q(qn(sat.getSatisfiedRequirement())));
            parts.add("\"by\":" + q(qn(e.getOwner())));
        }
        return "{" + String.join(",", parts) + "}";
    }

    public static void main(String[] args) throws Exception {
        int i = 0;
        String dump = null;
        if (args[0].equals("--dump")) { dump = args[1]; i = 2; }
        SysMLInteractive s = SysMLInteractive.getInstance();
        s.loadLibrary(args[i++]);
        int errors = 0, warnings = 0, files = 0;
        List<String> lines = new ArrayList<>();
        for (; i < args.length; i++) {
            files++;
            String text = new String(Files.readAllBytes(Paths.get(args[i])));
            SysMLInteractiveResult r = s.process(text);
            if (r.getException() != null) {
                errors++;
                System.out.println("ERROR " + args[i] + ": " + r.getException());
                continue;
            }
            for (var is : r.getIssues()) {
                boolean err = is.getSeverity().toString().equals("ERROR");
                if (err) errors++; else warnings++;
                System.out.println((err ? "ERROR " : "WARN ") + args[i] + ":" + is.getLineNumber() + ": " + is.getMessage());
            }
            if (dump != null && r.getRootElement() != null) {
                Element root = r.getRootElement();
                List<Element> all = new ArrayList<>();
                all.add(root);
                root.eAllContents().forEachRemaining(o -> { if (o instanceof Element) all.add((Element) o); });
                for (Element e : all) {
                    if (e instanceof Membership || e instanceof Documentation || e instanceof LiteralExpression) continue;
                    if (e.getQualifiedName() == null && e.getDeclaredName() == null
                        && !(e instanceof SatisfyRequirementUsage || e instanceof ConnectorAsUsage)) continue;
                    String d = describe(e);
                    lines.add(d.substring(0, d.length() - 1) + ",\"file\":" + q(args[i]) + "}");
                }
            }
        }
        if (dump != null) Files.write(Paths.get(dump), lines);
        System.out.println("sysml-validate files=" + files + " errors=" + errors + " warnings=" + warnings);
        System.exit(errors == 0 ? 0 : 1);
    }
}
