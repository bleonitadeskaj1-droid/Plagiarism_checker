"""
Professional PDF Report Generator
Generates production-grade plagiarism detection reports in PDF format.

Features:
- Executive summary
- Detailed analysis
- Similarity charts and visualizations
- Risk classification
- Flagged sections with evidence
- Recommendations and actions
- Professional formatting
- Multi-page support
"""

import logging
import json
import io
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, Color
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak,
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.pdfgen import canvas

logger = logging.getLogger(__name__)


class PlagiarismReportGenerator:
    """
    Professional plagiarism detection report generator.
    
    Creates comprehensive PDF reports containing:
    - Executive Summary
    - Overall Similarity Score
    - Risk Classification
    - Detailed Analysis
    - Flagged Sections
    - Visualizations
    - Recommendations
    - Confidence Metrics
    """
    
    # Color scheme
    COLORS = {
        "critical": HexColor("#D32F2F"),  # Red
        "high": HexColor("#F57C00"),      # Orange
        "medium": HexColor("#FBC02D"),    # Yellow
        "low": HexColor("#388E3C"),       # Green
        "header": HexColor("#1A237E"),    # Navy
        "accent": HexColor("#0097A7"),    # Cyan
        "text": HexColor("#212121"),      # Dark gray
        "light": HexColor("#F5F5F5"),     # Light gray
    }
    
    # Risk level thresholds
    RISK_LEVELS = {
        "critical": (70, 100),
        "high": (50, 70),
        "medium": (25, 50),
        "low": (0, 25),
    }
    
    def __init__(self, output_path: Path = None):
        """
        Initialize report generator.
        
        Args:
            output_path: Directory to save reports
        """
        self.output_path = Path(output_path) if output_path else Path("./reports")
        self.output_path.mkdir(parents=True, exist_ok=True)
        
        # Custom styles
        self.styles = self._create_custom_styles()
    
    def _create_custom_styles(self) -> Dict:
        """Create custom paragraph and table styles."""
        base_styles = getSampleStyleSheet()
        custom = {}
        
        # Title style
        custom['title'] = ParagraphStyle(
            'CustomTitle',
            parent=base_styles['Heading1'],
            fontSize=28,
            textColor=self.COLORS["header"],
            spaceAfter=12,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        )
        
        # Heading styles
        custom['heading1'] = ParagraphStyle(
            'CustomHeading1',
            parent=base_styles['Heading2'],
            fontSize=16,
            textColor=self.COLORS["header"],
            spaceAfter=10,
            spaceBefore=10,
            fontName='Helvetica-Bold'
        )
        
        custom['heading2'] = ParagraphStyle(
            'CustomHeading2',
            parent=base_styles['Heading3'],
            fontSize=13,
            textColor=self.COLORS["accent"],
            spaceAfter=8,
            spaceBefore=8,
            fontName='Helvetica-Bold'
        )
        
        # Body text
        custom['body'] = ParagraphStyle(
            'CustomBody',
            parent=base_styles['BodyText'],
            fontSize=11,
            alignment=TA_JUSTIFY,
            spaceAfter=6,
            leading=14
        )
        
        # Small text
        custom['small'] = ParagraphStyle(
            'CustomSmall',
            parent=base_styles['Normal'],
            fontSize=9,
            textColor=HexColor("#666666"),
            spaceAfter=4
        )
        
        # Emphasized text
        custom['emphasis'] = ParagraphStyle(
            'CustomEmphasis',
            parent=base_styles['Normal'],
            fontSize=11,
            textColor=self.COLORS["accent"],
            fontName='Helvetica-Bold'
        )
        
        return custom
    
    def generate_report(
        self,
        thesis_title: str,
        analysis_result: Dict[str, Any],
        student_info: Optional[Dict[str, str]] = None,
        professor_info: Optional[Dict[str, str]] = None,
        university_info: Optional[Dict[str, str]] = None,
        filename: Optional[str] = None
    ) -> Path:
        """
        Generate comprehensive plagiarism report.
        
        Args:
            thesis_title: Title of the thesis
            analysis_result: Result from PlagiarismAnalysisResult.to_dict()
            student_info: Student information dict
            professor_info: Professor information dict
            university_info: University information dict
            filename: Output filename (auto-generated if None)
        
        Returns:
            Path to generated PDF
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"plagiarism_report_{timestamp}.pdf"
        
        file_path = self.output_path / filename
        
        try:
            # Create PDF
            doc = SimpleDocTemplate(
                str(file_path),
                pagesize=letter,
                topMargin=0.5 * inch,
                bottomMargin=0.5 * inch,
                leftMargin=0.75 * inch,
                rightMargin=0.75 * inch,
            )
            
            # Build story (content)
            story = []
            
            # Add header with page numbers
            doc.build(
                self._build_content(
                    thesis_title, analysis_result,
                    student_info, professor_info, university_info
                ),
                onFirstPage=self._add_header,
                onLaterPages=self._add_header,
            )
            
            logger.info(f"Report generated: {file_path}")
            return file_path
        
        except Exception as e:
            logger.error(f"Error generating report: {e}")
            raise
    
    def _build_content(
        self,
        thesis_title: str,
        analysis_result: Dict[str, Any],
        student_info: Optional[Dict[str, str]],
        professor_info: Optional[Dict[str, str]],
        university_info: Optional[Dict[str, str]]
    ) -> List:
        """Build report content."""
        story = []
        
        # Title page
        story.extend(self._build_title_page(
            thesis_title, analysis_result, student_info, 
            professor_info, university_info
        ))
        story.append(PageBreak())
        
        # Executive summary
        story.extend(self._build_executive_summary(thesis_title, analysis_result))
        story.append(PageBreak())
        
        # Detailed analysis
        story.extend(self._build_detailed_analysis(analysis_result))
        
        if analysis_result.get("matches"):
            story.append(PageBreak())
            story.extend(self._build_flagged_sections(analysis_result["matches"]))
        
        story.append(PageBreak())
        story.extend(self._build_recommendations(analysis_result))
        
        return story
    
    def _build_title_page(
        self,
        thesis_title: str,
        analysis_result: Dict[str, Any],
        student_info: Optional[Dict],
        professor_info: Optional[Dict],
        university_info: Optional[Dict]
    ) -> List:
        """Build title page."""
        elements = []
        
        # Top spacing
        elements.append(Spacer(1, 1 * inch))
        
        # Title
        elements.append(Paragraph("PLAGIARISM DETECTION REPORT", self.styles['title']))
        elements.append(Spacer(1, 0.3 * inch))
        
        # Thesis title
        thesis_para = Paragraph(
            f"<b>{thesis_title}</b>",
            ParagraphStyle('ThesisTitle', parent=self.styles['body'], 
                          fontSize=14, alignment=TA_CENTER, textColor=self.COLORS["header"])
        )
        elements.append(thesis_para)
        elements.append(Spacer(1, 0.5 * inch))
        
        # Key metrics
        risk_level = analysis_result.get("risk_level", "unknown").upper()
        score = analysis_result.get("overall_score", 0)
        risk_color = self.COLORS.get(risk_level.lower(), self.COLORS["low"])
        
        metrics_data = [
            ["Overall Similarity", f"{score}%"],
            ["Risk Level", risk_level],
            ["Confidence", f"{analysis_result.get('confidence_score', 0)}%"],
            ["Flagged Sections", len(analysis_result.get("matches", []))],
        ]
        
        metrics_table = Table(metrics_data, colWidths=[3*inch, 2*inch])
        metrics_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), self.COLORS["light"]),
            ('BACKGROUND', (1, 0), (1, -1), self.COLORS["header"]),
            ('TEXTCOLOR', (1, 0), (1, -1), HexColor("#FFFFFF")),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 11),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('TOPPADDING', (0, 0), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1, HexColor("#CCCCCC")),
        ]))
        elements.append(metrics_table)
        
        elements.append(Spacer(1, 0.5 * inch))
        
        # Report info
        report_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        elements.append(Paragraph(
            f"Report Generated: {report_date}",
            self.styles['small']
        ))
        
        if student_info:
            student_name = student_info.get("name", "Unknown")
            elements.append(Paragraph(f"Student: {student_name}", self.styles['small']))
        
        if professor_info:
            prof_name = professor_info.get("name", "Unknown")
            elements.append(Paragraph(f"Reviewed by: {prof_name}", self.styles['small']))
        
        return elements
    
    def _build_executive_summary(
        self,
        thesis_title: str,
        analysis_result: Dict[str, Any]
    ) -> List:
        """Build executive summary section."""
        elements = []
        
        elements.append(Paragraph("EXECUTIVE SUMMARY", self.styles['heading1']))
        elements.append(Spacer(1, 0.2 * inch))
        
        score = analysis_result.get("overall_score", 0)
        risk_level = analysis_result.get("risk_level", "unknown").upper()
        confidence = analysis_result.get("confidence_score", 0)
        
        summary_text = f"""
        <b>Thesis:</b> {thesis_title}<br/>
        <b>Overall Similarity Score:</b> <font color="{self.COLORS[risk_level.lower()].hexval()}"><b>{score}%</b></font><br/>
        <b>Risk Classification:</b> {risk_level}<br/>
        <b>Analysis Confidence:</b> {confidence}%<br/>
        <b>Flagged Sections:</b> {len(analysis_result.get("matches", []))}<br/>
        <br/>
        This report contains a comprehensive plagiarism analysis of the submitted thesis.
        The similarity score indicates the percentage of content that matches existing documents
        in our database. The risk level classification helps determine the level of concern.
        """
        
        elements.append(Paragraph(summary_text, self.styles['body']))
        
        # Risk explanation
        elements.append(Spacer(1, 0.2 * inch))
        elements.append(Paragraph("RISK LEVEL INTERPRETATION", self.styles['heading2']))
        
        risk_explanations = {
            "CRITICAL": "≥70% - Strong indicators of plagiarism. Immediate review required.",
            "HIGH": "50-69% - Significant similarity detected. Detailed review needed.",
            "MEDIUM": "25-49% - Moderate similarity. Standard review recommended.",
            "LOW": "0-24% - Minimal similarity. Likely original work."
        }
        
        for level, explanation in risk_explanations.items():
            color = self.COLORS.get(level.lower(), self.COLORS["low"])
            elements.append(Paragraph(
                f"<font color='{color.hexval()}'><b>{level}:</b></font> {explanation}",
                self.styles['body']
            ))
        
        return elements
    
    def _build_detailed_analysis(self, analysis_result: Dict[str, Any]) -> List:
        """Build detailed analysis section."""
        elements = []
        
        elements.append(Paragraph("DETAILED ANALYSIS", self.styles['heading1']))
        elements.append(Spacer(1, 0.2 * inch))
        
        # Similarity breakdown
        elements.append(Paragraph("Similarity Breakdown", self.styles['heading2']))
        
        breakdown_data = [
            ["Source", "Similarity Score"],
            ["Internal Database", f"{analysis_result.get('internal_score', 0)}%"],
            ["Confidential Documents", f"{analysis_result.get('confidential_score', 0)}%"],
            ["Web Sources", f"{analysis_result.get('web_score', 0)}%"],
        ]
        
        breakdown_table = Table(breakdown_data, colWidths=[3.5*inch, 1.5*inch])
        breakdown_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), self.COLORS["header"]),
            ('TEXTCOLOR', (0, 0), (-1, 0), HexColor("#FFFFFF")),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 11),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 1, HexColor("#CCCCCC")),
        ]))
        elements.append(breakdown_table)
        
        elements.append(Spacer(1, 0.3 * inch))
        
        # Analysis metadata
        elements.append(Paragraph("Analysis Metadata", self.styles['heading2']))
        
        metadata = analysis_result.get("metadata", {})
        metadata_text = f"""
        <b>Documents Compared:</b> {metadata.get('documents_compared', 0)}<br/>
        <b>Text Length:</b> {metadata.get('text_length', 0)} characters<br/>
        <b>Language Detected:</b> {metadata.get('language', 'Unknown')}<br/>
        <b>Execution Time:</b> {analysis_result.get('execution_time_ms', 0):.0f}ms<br/>
        <b>Analysis Timestamp:</b> {datetime.fromtimestamp(metadata.get('analysis_timestamp', 0)).strftime('%Y-%m-%d %H:%M:%S')}<br/>
        """
        
        elements.append(Paragraph(metadata_text, self.styles['body']))
        
        return elements
    
    def _build_flagged_sections(self, matches: List[Dict[str, Any]]) -> List:
        """Build flagged sections detail."""
        elements = []
        
        elements.append(Paragraph("FLAGGED SECTIONS", self.styles['heading1']))
        elements.append(Spacer(1, 0.2 * inch))
        
        for i, match in enumerate(matches[:10], 1):  # Top 10 matches
            elements.append(Paragraph(f"Match #{i}", self.styles['heading2']))
            
            match_text = f"""
            <b>Detection Method:</b> {match.get('method', 'unknown')}<br/>
            <b>Source:</b> {match.get('source_title', 'Unknown')}<br/>
            <b>Similarity:</b> {match.get('similarity_score', 0):.1f}%<br/>
            <b>Confidence:</b> {match.get('confidence', 0):.0%}<br/>
            <br/>
            <b>Original Text:</b><br/>
            <font face="Courier" size="9">{match.get('original_text', '')[:200]}...</font><br/>
            <br/>
            <b>Source Text:</b><br/>
            <font face="Courier" size="9">{match.get('source_text', '')[:200]}...</font>
            """
            
            elements.append(Paragraph(match_text, self.styles['small']))
            elements.append(Spacer(1, 0.2 * inch))
        
        return elements
    
    def _build_recommendations(self, analysis_result: Dict[str, Any]) -> List:
        """Build recommendations section."""
        elements = []
        
        elements.append(Paragraph("RECOMMENDATIONS", self.styles['heading1']))
        elements.append(Spacer(1, 0.2 * inch))
        
        recommendations = analysis_result.get("recommendations", [])
        for recommendation in recommendations:
            elements.append(Paragraph(f"• {recommendation}", self.styles['body']))
        
        return elements
    
    def _add_header(self, canvas_obj, doc):
        """Add header to each page."""
        canvas_obj.saveState()
        
        # Add title in header
        canvas_obj.setFont("Helvetica-Bold", 10)
        canvas_obj.drawString(0.75*inch, letter[1] - 0.4*inch, "Plagiarism Detection Report")
        
        # Add page number
        canvas_obj.setFont("Helvetica", 9)
        page_num = doc.page
        canvas_obj.drawRightString(7.75*inch, letter[1] - 0.4*inch, f"Page {page_num}")
        
        # Horizontal line
        canvas_obj.setLineWidth(0.5)
        canvas_obj.line(0.75*inch, letter[1] - 0.5*inch, 7.75*inch, letter[1] - 0.5*inch)
        
        canvas_obj.restoreState()
    
    def generate_json_report(
        self,
        analysis_result: Dict[str, Any],
        filename: Optional[str] = None
    ) -> Path:
        """
        Generate JSON report for programmatic access.
        
        Args:
            analysis_result: Analysis result dictionary
            filename: Output filename
        
        Returns:
            Path to generated JSON file
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"plagiarism_report_{timestamp}.json"
        
        file_path = self.output_path / filename
        
        with open(file_path, "w") as f:
            json.dump(analysis_result, f, indent=2, default=str)
        
        logger.info(f"JSON report generated: {file_path}")
        return file_path
