package tema2.t01;

import java.util.Scanner;

public class ej15 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);

        System.out.println("Digite un numero para mostrarte su tabla de multiplicacion:");
        int n=sc.nextInt();

        for (int i=1;i<=10;i++)
        {
            System.out.println(n + "*" + i + " = " + n*i);
        }
    }
}
