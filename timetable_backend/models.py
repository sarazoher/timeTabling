from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class Course:
    course_id: int
    name: str
    prerequisites: Optional[str]
    parallel_requirement: Optional[str]
    year: int
    lecturer: str
    credits: float
    weekly_hours: float
    course_type: str
    course_category: str
    lecture_hours: float
    tutorial_hours: float
    calculation_status: str
    original_semester: Optional[int] = None
    flexibility: str = "auto"  # fixed | flexible | auto
    # Set only when an approved/automatic year move changed `year`
    # (year_plan.py); holds the study year written in the Excel.
    original_year: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)
