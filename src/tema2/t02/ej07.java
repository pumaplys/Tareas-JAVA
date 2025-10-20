package tema2.t02;

import java.util.Scanner;

public class ej07 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);

        System.out.println("Introduce el primer numero");
        int num1 = sc.nextInt();
        System.out.println("Introduce el segundo numero");
        int num2 = sc.nextInt();

        for (int i = 1; i <= 10; i++){
            int randomNum = (int)(Math.random() * (num2 - num1 + 1) + num1);
            System.out.println(randomNum);
        }
    }
}
