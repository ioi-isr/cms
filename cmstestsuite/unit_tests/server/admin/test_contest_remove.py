#!/usr/bin/env python3

import unittest
from uuid import uuid4

from cms.db import Contest, Task, SessionGen
from cms.server.admin.handlers.contest import remove_contest_with_action
from sqlalchemy import func


def uid():
    """Generate a unique identifier for test objects."""
    return uuid4().hex[:8]


class TestContestRemoveWithTaskHandling(unittest.TestCase):
    """Unit tests for contest deletion with task handling options."""

    def test_move_tasks_to_another_contest_preserves_order(self):
        """Test that moving tasks to another contest preserves their relative order."""
        with SessionGen() as session:
            source_contest = Contest(name=f"source_contest_{uid()}", description="Source")
            target_contest = Contest(name=f"target_contest_{uid()}", description="Target")
            session.add(source_contest)
            session.add(target_contest)
            session.flush()
            
            task1 = Task(name=f"task1_{uid()}", title="Task 1", contest=source_contest, num=0)
            task2 = Task(name=f"task2_{uid()}", title="Task 2", contest=source_contest, num=1)
            task3 = Task(name=f"task3_{uid()}", title="Task 3", contest=source_contest, num=2)
            session.add_all([task1, task2, task3])
            session.flush()
            
            task1_name = task1.name
            task2_name = task2.name
            task3_name = task3.name
            
            remove_contest_with_action(session, source_contest, "move", target_contest)
            session.commit()
            
            moved_tasks = session.query(Task)\
                .filter(Task.contest == target_contest)\
                .order_by(Task.num)\
                .all()
            
            self.assertEqual(len(moved_tasks), 3)
            self.assertEqual(moved_tasks[0].name, task1_name)
            self.assertEqual(moved_tasks[1].name, task2_name)
            self.assertEqual(moved_tasks[2].name, task3_name)
            self.assertEqual(moved_tasks[0].num, 0)
            self.assertEqual(moved_tasks[1].num, 1)
            self.assertEqual(moved_tasks[2].num, 2)

    def test_detach_tasks_sets_contest_and_num_to_null(self):
        """Test that detaching tasks sets contest_id and num to NULL."""
        with SessionGen() as session:
            contest = Contest(name=f"test_contest_{uid()}", description="Test")
            session.add(contest)
            session.flush()
            
            task1 = Task(name=f"task1_{uid()}", title="Task 1", contest=contest, num=0)
            task2 = Task(name=f"task2_{uid()}", title="Task 2", contest=contest, num=1)
            session.add_all([task1, task2])
            session.flush()
            
            task_ids = [task1.id, task2.id]
            
            remove_contest_with_action(session, contest, "detach", None)
            session.commit()
            
            detached_tasks = session.query(Task)\
                .filter(Task.id.in_(task_ids))\
                .all()
            
            self.assertEqual(len(detached_tasks), 2)
            for task in detached_tasks:
                self.assertIsNone(task.contest)
                self.assertIsNone(task.contest_id)
                self.assertIsNone(task.num)

    def test_delete_all_tasks_cascades_deletion(self):
        """Test that delete_all action cascades and deletes all tasks."""
        with SessionGen() as session:
            contest = Contest(name=f"test_contest_{uid()}", description="Test")
            session.add(contest)
            session.flush()
            
            task1 = Task(name=f"task1_{uid()}", title="Task 1", contest=contest, num=0)
            task2 = Task(name=f"task2_{uid()}", title="Task 2", contest=contest, num=1)
            session.add_all([task1, task2])
            session.flush()
            
            task_ids = [task1.id, task2.id]
            
            remove_contest_with_action(session, contest, "delete_all", None)
            session.commit()
            
            remaining_tasks = session.query(Task)\
                .filter(Task.id.in_(task_ids))\
                .all()
            
            self.assertEqual(len(remaining_tasks), 0)

    def test_move_tasks_with_gaps_in_target_contest(self):
        """Test moving tasks when target contest has gaps in num values.
        
        Gaps in num values can occur during imports or manual database edits.
        This test verifies that the move logic correctly appends tasks after
        the maximum num value, even when gaps exist in the target contest.
        """
        with SessionGen() as session:
            source_contest = Contest(name=f"source_contest_{uid()}", description="Source")
            target_contest = Contest(name=f"target_contest_{uid()}", description="Target")
            session.add(source_contest)
            session.add(target_contest)
            session.flush()
            
            target_task1 = Task(name=f"target1_{uid()}", title="Target 1", 
                               contest=target_contest, num=0)
            target_task2 = Task(name=f"target2_{uid()}", title="Target 2", 
                               contest=target_contest, num=5)
            source_task = Task(name=f"source1_{uid()}", title="Source 1", 
                              contest=source_contest, num=0)
            session.add_all([target_task1, target_task2, source_task])
            session.flush()
            
            source_task_name = source_task.name
            
            remove_contest_with_action(session, source_contest, "move", target_contest)
            session.commit()
            
            all_tasks = session.query(Task)\
                .filter(Task.contest == target_contest)\
                .order_by(Task.num)\
                .all()
            
            self.assertEqual(len(all_tasks), 3)
            self.assertEqual(all_tasks[0].num, 0)
            self.assertEqual(all_tasks[1].num, 5)
            self.assertEqual(all_tasks[2].num, 6)
            self.assertEqual(all_tasks[2].name, source_task_name)


if __name__ == '__main__':
    unittest.main()
